"""MCP Server for SQL Database Access

This MCP server provides tools and resources for querying the money_rag SQLite database.
It exposes:
- Resources: Database schema information
- Tools: SQL query execution
"""

import os
import sqlite3
from fastmcp import FastMCP

# Database configuration
DB_PATH = os.path.join(os.path.dirname(__file__), "notebooks", "money_rag.db")

# Create FastMCP server instance
mcp = FastMCP("money-rag-sql-server")


def get_schema_info() -> str:
    """Get database schema information."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()

    schema_info = []
    for (table_name,) in tables:
        schema_info.append(f"\nTable: {table_name}")

        # Get column info for each table
        cursor.execute(f"PRAGMA table_info({table_name});")
        columns = cursor.fetchall()

        schema_info.append("Columns:")
        for col in columns:
            col_id, col_name, col_type, not_null, default_val, pk = col
            schema_info.append(f"  - {col_name} ({col_type})")

    conn.close()
    return "\n".join(schema_info)


@mcp.resource("schema://database/tables")
def get_database_schema() -> str:
    """Complete schema information for the money_rag database."""
    return get_schema_info()


@mcp.tool()
def query_database(query: str) -> str:
    """Execute a SELECT query on the money_rag database.

    Args:
        query: The SQL SELECT query to execute

    Returns:
        Query results or error message

    Important Notes:
    - Only SELECT queries are allowed (read-only)
    - Use 'description' column for text search 
    - 'amount' column: positive values = spending, negative values = payments/refunds

    Example queries:
    - Find Walmart spending: SELECT SUM(amount) FROM transactions WHERE description LIKE '%Walmart%' AND amount > 0;
    - List recent transactions: SELECT transaction_date, description, amount, category FROM transactions ORDER BY transaction_date DESC LIMIT 5;
    - Spending by category: SELECT category, SUM(amount) FROM transactions WHERE amount > 0 GROUP BY category;
    """
    # Security: Only allow SELECT queries
    query_upper = query.strip().upper()
    if not query_upper.startswith("SELECT") and not query_upper.startswith("PRAGMA"):
        return "Error: Only SELECT and PRAGMA queries are allowed"

    # Forbidden operations
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE", "TRUNCATE"]
    if any(forbidden_word in query_upper for forbidden_word in forbidden):
        return f"Error: Query contains forbidden operation. Only SELECT queries allowed."

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(query)
        results = cursor.fetchall()
        conn.close()

        if not results:
            return "No results found"

        return str(results)
    except sqlite3.Error as e:
        return f"Error: {str(e)}"
