import pandas as pd
import sqlite3
from langchain.chat_models import init_chat_model
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

def ingest_csv(file_path, llm, db_path="money_rag.db"):
    df = pd.read_csv(file_path)
    headers = df.columns.tolist()
    sample_data = df.head(5).to_json() # Increased sample to 5 for better context

    prompt = ChatPromptTemplate.from_template("""
    I have a CSV with these headers: {headers}
    Sample data: {sample}
    
    Tasks:
    1. Map headers to my standard schema: date_col, desc_col, amount_col, category_col, type
    2. Identify type:
        - type = payment type, use sign from amount to extract the type 
        - If it is a credit, return "credit"
        - If it is a debit, return "debit"
        - Sign conventions are of two types:
            - Positive : Positive means credit
            - Negative: Negative means debit 
        - {filename} : from the filename find the source
                - Positive convention : if the source is Discover 
                - Negative convention : if the source is Chase
            
             
    3. Remove the sign from amount to map to amount_col 
                                        
    2. Identify 'sign_convention': 
       - Look at the 'amount_col' column. 
       - If a 'Payment' or 'Credit' is a NEGATIVE number, return "payment_is_negative".
       - If a 'Payment' or 'Credit' is a POSITIVE number, return "payment_is_positive".
    
    Return ONLY JSON with keys: date_col, desc_col, amount_col, category_col, sign_convention.
                                              
    Understand that the conventions work like this:
    1. Credit convention: 
    """)
    
    chain = prompt | llm | JsonOutputParser()
    mapping = chain.invoke({"headers": headers, "sample": sample_data, "filename": file_path})

    standard_df = pd.DataFrame()
    standard_df['transaction_date'] = pd.to_datetime(df[mapping['date_col']])
    standard_df['description'] = df[mapping['desc_col']]
    
    # --- Normalization Logic ---
    raw_amounts = pd.to_numeric(df[mapping['amount_col']])
    
    # We want a standard: Spending is POSITIVE, Payments/Refunds are NEGATIVE
    if mapping.get('sign_convention') == "payment_is_positive":
        # Flip the signs so payments become negative
        standard_df['amount'] = raw_amounts * -1
    else:
        # Already consistent (spending +, payments -)
        standard_df['amount'] = raw_amounts
    
    standard_df['category'] = df[mapping['category_col']] if mapping.get('category_col') else 'Uncategorized'
    standard_df['source_file'] = file_path

    conn = sqlite3.connect(db_path)
    standard_df.to_sql("transactions", conn, if_exists="append", index=False)
    conn.close()
    
    print(f"✅ Ingested {len(standard_df)} rows. Convention: {mapping.get('sign_convention')}")

# --- Example Usage with Different Providers ---

# To use Gemini, you'd install: pip install -U langchain-google-genai
# To use Vertex AI, you'd install: pip install -qU langchain-google-vertexai

# Option A: OpenAI
# llm = init_chat_model("gpt-4o", model_provider="openai", temperature=0)

# Option B: Google Gemini (Developer API)
# llm_gemini = init_chat_model("gemini-2.5-flash", model_provider="google_genai", temperature=0)

# Option C: Google Vertex AI (GCP Enterprise)
# llm_vertex = init_chat_model("gemini-1.5-pro", model_provider="google_vertexai", temperature=0)

# ingest_csv("discover_june.csv", llm_gemini)