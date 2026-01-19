import os
import uuid
import asyncio
import pandas as pd
import sqlite3
import shutil
import tempfile
from typing import List, Optional
from dataclasses import dataclass

from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.tools import tool
from langchain_community.utilities import SQLDatabase
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams
from langgraph.runtime import get_runtime
from langgraph.checkpoint.memory import InMemorySaver
from langchain.agents import create_agent
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_mcp_adapters.client import MultiServerMCPClient  

# Import specific embeddings
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_openai import OpenAIEmbeddings

class MoneyRAG:
    def __init__(self, llm_provider: str, model_name: str, embedding_model_name: str, api_key: str):
        self.llm_provider = llm_provider.lower()
        self.model_name = model_name
        self.embedding_model_name = embedding_model_name
        
        # Set API Keys
        if self.llm_provider == "google":
            os.environ["GOOGLE_API_KEY"] = api_key
            self.embeddings = GoogleGenerativeAIEmbeddings(model=embedding_model_name)
            provider_name = "google_genai"
        else:
            os.environ["OPENAI_API_KEY"] = api_key
            self.embeddings = OpenAIEmbeddings(model=embedding_model_name)
            provider_name = "openai"

        # Initialize LLM
        self.llm = init_chat_model(
            self.model_name,
            model_provider=provider_name,
        )

        # Temporary paths for this session
        self.temp_dir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.temp_dir # Harmonize with mcp_server.py 
        self.db_path = os.path.join(self.temp_dir, "money_rag.db")
        self.qdrant_path = os.path.join(self.temp_dir, "qdrant_db")
        
        self.db: Optional[SQLDatabase] = None
        self.vector_store: Optional[QdrantVectorStore] = None
        self.agent = None
        self.mcp_client: Optional[MultiServerMCPClient] = None
        self.search_tool = DuckDuckGoSearchRun()
        self.merchant_cache = {}  # Session-based cache for merchant enrichment

    async def setup_session(self, csv_paths: List[str]):
        """Ingests CSVs and sets up DBs."""
        for path in csv_paths:
            await self._ingest_csv(path)
        
        self.db = SQLDatabase.from_uri(f"sqlite:///{self.db_path}")
        self.vector_store = self._sync_to_qdrant()
        await self._init_agent()

    async def _ingest_csv(self, file_path):
        df = pd.read_csv(file_path)
        headers = df.columns.tolist()
        sample_data = df.head(10).to_json()

        prompt = ChatPromptTemplate.from_template("""
        Act as a financial data parser. Analyze this CSV data:
        Filename: {filename}
        Headers: {headers}
        Sample Data: {sample}

        TASK:
        1. Map the CSV columns to standard fields: date, description, amount, and category.
        2. Determine the 'sign_convention' for spending.
        
        RULES:
        - If the filename suggests 'Discover' credit card, spending are usually POSITIVE.
        - If the filename suggests 'Chase' credit card, spending are usually NEGATIVE.
                                                
        - Analyze the 'sign_convention' for spending (outflows):
            - Look at the sample data for known merchants or spending patterns.
            - If spending (like a restaurant or store) is NEGATIVE (e.g., -25.00), the convention is 'spending_is_negative'.
            - If spending is POSITIVE (e.g., 25.00), the convention is 'spending_is_positive'.

        OUTPUT FORMAT (JSON ONLY):
        {{
        "date_col": "column_name",
        "desc_col": "column_name",
        "amount_col": "column_name",
        "category_col": "column_name or null",
        "sign_convention": "spending_is_negative" | "spending_is_positive"
        }}
        """)
        
        chain = prompt | self.llm | JsonOutputParser()
        mapping = await chain.ainvoke({"headers": headers, "sample": sample_data, "filename": os.path.basename(file_path)})

        standard_df = pd.DataFrame()
        standard_df['id'] = [str(uuid.uuid4()) for _ in range(len(df))]
        standard_df['transaction_date'] = pd.to_datetime(df[mapping['date_col']])
        standard_df['description'] = df[mapping['desc_col']]
        
        raw_amounts = pd.to_numeric(df[mapping['amount_col']])
        standard_df['amount'] = raw_amounts * -1 if mapping['sign_convention'] == "spending_is_negative" else raw_amounts
        standard_df['category'] = df[mapping.get('category_col')] if mapping.get('category_col') else 'Uncategorized'
        standard_df['source_file'] = os.path.basename(file_path)

        # --- Async Enrichment Step ---
        print(f"   ✨ Enriching descriptions for {os.path.basename(file_path)}...")
        unique_descriptions = standard_df['description'].unique()
        sem = asyncio.Semaphore(5)

        async def get_merchant_info(description):
            if description in self.merchant_cache:
                return self.merchant_cache[description]
            
            async with sem:
                try:
                    await asyncio.sleep(0.05) # Jitter
                    print(f"      🔍 Web searching: {description}...")
                    result = await self.search_tool.ainvoke(f"What type of business / store is '{description}'?")
                    self.merchant_cache[description] = result
                    return result
                except Exception as e:
                    print(f"      ⚠️ Search failed for {description}: {e}")
                    return "Unknown"

        tasks = [get_merchant_info(desc) for desc in unique_descriptions]
        enrichment_results = await asyncio.gather(*tasks)
        
        desc_map = dict(zip(unique_descriptions, enrichment_results))
        standard_df['enriched_info'] = standard_df['description'].map(desc_map).fillna("")

        conn = sqlite3.connect(self.db_path)
        standard_df.to_sql("transactions", conn, if_exists="append", index=False)
        conn.close()

    def _sync_to_qdrant(self):
        client = QdrantClient(path=self.qdrant_path)
        collection = "transactions"
        
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM transactions", conn)
        conn.close()
        
        # Check for empty dataframe
        if df.empty:
            raise ValueError("No transactions found in database. Please ingest CSV files first.")
        
        # Dynamically detect embedding dimension
        sample_embedding = self.embeddings.embed_query("test")
        embedding_dim = len(sample_embedding)

        client.recreate_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=embedding_dim, distance=Distance.COSINE),
        )
        
        vs = QdrantVectorStore(client=client, collection_name=collection, embedding=self.embeddings)
        
        # Use description + category + enrichment for vectorization
        texts = []
        for _, row in df.iterrows():
            enriched = row.get('enriched_info', '')
            base_text = f"{row['description']} ({row['category']})"
            if enriched and enriched != "Unknown" and enriched != "":
                texts.append(f"{base_text} - {enriched}")
            else:
                texts.append(base_text)
        
        metadatas = df[['id', 'amount', 'category', 'transaction_date']].to_dict('records')
        for m in metadatas: m['transaction_date'] = str(m['transaction_date'])
        
        vs.add_texts(texts=texts, metadatas=metadatas)
        return vs

    async def _init_agent(self):
        # 1. Initialize MCP client with absolute path to server
        server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")
        
        self.mcp_client = MultiServerMCPClient(
            {
                "money_rag": {
                    "transport": "stdio",
                    "command": "python",
                    "args": [server_path],
                    "env": os.environ.copy(),
                }
            }
        )

        # 2. Get tools from MCP server
        mcp_tools = await self.mcp_client.get_tools()

        # 3. Define the Agent with MCP Tools
        system_prompt = (
            "You are a financial analyst. Use the provided tools to query the database "
            "and perform semantic searches. Spending is POSITIVE (>0). "
            "Always explain your findings clearly."
        )
        
        self.agent = create_agent(
            model=self.llm,
            tools=mcp_tools,
            system_prompt=system_prompt,
            checkpointer=InMemorySaver(),
        )

    async def chat(self, query: str):
        config = {"configurable": {"thread_id": "session_1"}}
        
        result = await self.agent.ainvoke(
            {"messages": [{"role": "user", "content": query}]},
            config,
        )
        
        # Extract content - handle both string and list formats
        content = result["messages"][-1].content
        
        # If content is a list (Gemini format), extract text from blocks
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            return "\n".join(text_parts)
        
        # If content is already a string (OpenAI format), return as-is
        return content

    async def cleanup(self):
        """Delete temporary session files and close MCP client."""
        if self.mcp_client:
            try:
                await self.mcp_client.close()
            except Exception as e:
                print(f"Warning: Failed to close MCP client: {e}")
        
        if os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                print(f"Warning: Failed to remove temp directory: {e}")