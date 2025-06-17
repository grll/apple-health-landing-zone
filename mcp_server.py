import gradio as gr
import sqlite3
import pandas as pd
import json
import os
from huggingface_hub import hf_hub_download

# Configuration
LOCAL_DB_PATH = "data/health_data.db"
DATA_REPO = os.getenv("DATA_REPO", None)  # e.g., "username/project-data"
IS_HF_SPACE = os.getenv("SPACE_ID") is not None

def get_db_connection(token=None):
    """Get a connection to the SQLite database."""
    if DATA_REPO and IS_HF_SPACE:
        # Running in HF Spaces - download from private dataset
        try:
            db_path = hf_hub_download(
                repo_id=DATA_REPO,
                filename="health_data.db",
                repo_type="dataset",
                token=token
            )
            return sqlite3.connect(db_path)
        except Exception as e:
            raise Exception(f"Failed to download database from {DATA_REPO}: {str(e)}")
    else:
        # Local development mode
        if not os.path.exists(LOCAL_DB_PATH):
            raise FileNotFoundError(f"Database file not found: {LOCAL_DB_PATH}. Run create_test_db.py first.")
        return sqlite3.connect(LOCAL_DB_PATH)

def execute_sql_query(sql_query, hf_token=None):
    """Execute any SQL query on the Apple Health SQLite database.
    
    Args:
        sql_query (str): The SQL query to execute
        hf_token (str): Hugging Face token for accessing private dataset
        
    Returns:
        str: JSON formatted results or error message
    """
    if not sql_query or not sql_query.strip():
        return "Error: Empty SQL query provided"
    
    try:
        conn = get_db_connection(token=hf_token)
        
        # Execute the query
        result = pd.read_sql_query(sql_query, conn)
        conn.close()
        
        # Convert to JSON
        return json.dumps(result.to_dict('records'), indent=2)
        
    except Exception as e:
        return f"Error executing SQL query: {str(e)}"

# MCP Server Interface
with gr.Blocks(title="Apple Health MCP Server") as demo:
    if IS_HF_SPACE and DATA_REPO:
        gr.Markdown("# Apple Health MCP Server")
        gr.Markdown(f"This is an MCP server for querying Apple Health data from dataset: `{DATA_REPO}`")
    else:
        gr.Markdown("# Apple Health MCP Server (Local Development)")
        gr.Markdown(f"Database: `{LOCAL_DB_PATH}`")
    
    with gr.Tab("SQL Query Interface"):
        gr.Markdown("### Execute SQL Queries")
        gr.Markdown("Enter any SQL query to execute against your Apple Health SQLite database.")
        
        # Only show token input if running in HF Spaces
        if IS_HF_SPACE and DATA_REPO:
            hf_token_input = gr.Textbox(
                label="Hugging Face Token",
                placeholder="hf_...",
                type="password",
                info="Your HF token to access the private dataset. Get it from https://huggingface.co/settings/tokens"
            )
        else:
            hf_token_input = gr.Textbox(visible=False, value=None)
        
        sql_input = gr.Textbox(
            label="SQL Query",
            placeholder="SELECT * FROM activity_summaries LIMIT 10;",
            lines=5,
            value="SELECT name FROM sqlite_master WHERE type='table';",
            info="Available tables depend on your export data. Run the first sample query to see all tables."
        )
        
        query_btn = gr.Button("Execute Query", variant="primary")
        output = gr.Code(language="json", label="Query Results")
        
        # Sample queries for easy testing
        gr.Markdown("### Sample Queries")
        gr.Examples(
            examples=[
                ["SELECT name FROM sqlite_master WHERE type='table';"],
                ["SELECT * FROM workout LIMIT 5;"],
                ["SELECT * FROM instantaneousbeatsperminute;"],
            ],
            inputs=sql_input
        )
        
        query_btn.click(
            fn=execute_sql_query,
            inputs=[sql_input, hf_token_input],
            outputs=output
        )
    
    with gr.Tab("MCP Endpoint"):
        if IS_HF_SPACE:
            space_id = os.getenv("SPACE_ID", "your-space-id")
            gr.Markdown(f"""
            ## MCP Server Endpoint
            
            This space can be used as an MCP server with the following configuration:
            
            ```json
            {{
                "mcpServers": {{
                    "apple-health": {{
                        "command": "npx",
                        "args": [
                            "mcp-remote",
                            "https://huggingface.co/spaces/{space_id}/gradio_api/mcp/sse",
                            "--header",
                            "Authorization:${{AUTH_HEADER}}"
                        ],
                        "env": {{
                            "AUTH_HEADER": "Bearer YOUR_HF_TOKEN_HERE"
                        }}
                    }}
                }}
            }}
            ```
            
            **Setup Instructions:**
            1. Replace `YOUR_HF_TOKEN_HERE` with your actual Hugging Face token
            2. Add this configuration to your Claude Desktop config file
            3. Claude will be able to query your Apple Health data using SQL
            """)
        else:
            gr.Markdown("""
            ## MCP Server Endpoint
            
            This local server can be used as an MCP server with the following configuration:
            
            ```json
            {
                "mcpServers": {
                    "apple-health-local": {
                        "command": "npx",
                        "args": [
                            "mcp-remote",
                            "http://localhost:7860/gradio_api/mcp/sse"
                        ]
                    }
                }
            }
            ```
            
            **Setup Instructions:**
            1. Run this server: `python mcp_server.py`
            2. Add the above configuration to your Claude Desktop config file
            3. Claude will be able to query your Apple Health data using SQL
            
            **Note:** This is a local development server. No authentication is required.
            """)

if __name__ == "__main__":
    if IS_HF_SPACE:
        print(f"Starting Apple Health MCP Server in HF Spaces mode")
        if DATA_REPO:
            print(f"Data repository: {DATA_REPO}")
        else:
            print("Warning: DATA_REPO environment variable not set")
    else:
        print(f"Starting Apple Health MCP Server (Local Development)")
        print(f"Database path: {LOCAL_DB_PATH}")
    
    demo.launch(mcp_server=True, server_name="0.0.0.0" if not IS_HF_SPACE else None)