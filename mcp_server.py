import gradio as gr
import sqlite3
import pandas as pd
import json
import os
import tempfile
from huggingface_hub import hf_hub_download, HfApi
from huggingface_hub.utils import EntryNotFoundError

# Configuration
LOCAL_DB_PATH = "data/health_data.db"
LOCAL_XML_PATH = "data/export.xml"
DATA_REPO = os.getenv("DATA_REPO", None)  # e.g., "username/project-data"
IS_HF_SPACE = os.getenv("SPACE_ID") is not None
HF_TOKEN = os.getenv("HF_TOKEN", None)  # Space secret for write access

def check_and_parse_if_needed():
    """Check if database exists and parse XML if not (both HF and local)."""
    if IS_HF_SPACE and DATA_REPO:
        # HF Spaces mode
        print(f"Checking if database exists in {DATA_REPO}...")
        api = HfApi(token=HF_TOKEN)
        
        try:
            # Try to download the database
            db_path = hf_hub_download(
                repo_id=DATA_REPO,
                filename="health_data.db",
                repo_type="dataset",
                token=HF_TOKEN
            )
            print("Database already exists in dataset.")
            return
        except (EntryNotFoundError, Exception) as e:
            print(f"Database not found, will parse export.xml: {str(e)}")
        
        try:
            # Download export.xml
            print("Downloading export.xml...")
            xml_path = hf_hub_download(
                repo_id=DATA_REPO,
                filename="export.xml",
                repo_type="dataset",
                token=HF_TOKEN
            )
            
            # Parse the XML file
            print("Parsing export.xml...")
            from src.parser.parser import AppleHealthParser
            
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = os.path.join(temp_dir, "health_data.db")
                
                # Parse with default date cutoff (6 months)
                parser = AppleHealthParser(db_path=db_path)
                parser.parse_file(xml_path)
                
                print("Uploading parsed database to dataset...")
                # Upload the database back to the dataset
                api.upload_file(
                    path_or_fileobj=db_path,
                    path_in_repo="health_data.db",
                    repo_id=DATA_REPO,
                    repo_type="dataset",
                    token=HF_TOKEN,
                    commit_message="Add parsed SQLite database from export.xml"
                )
                print("Successfully created and uploaded health_data.db")
                
        except Exception as e:
            print(f"Error during HF parsing: {str(e)}")
            raise
    else:
        # Local mode
        print(f"Checking if local database exists at {LOCAL_DB_PATH}...")
        
        if os.path.exists(LOCAL_DB_PATH):
            print("Local database already exists.")
            return
        
        if not os.path.exists(LOCAL_XML_PATH):
            print(f"Warning: Neither database ({LOCAL_DB_PATH}) nor XML file ({LOCAL_XML_PATH}) found.")
            return
        
        try:
            print(f"Parsing local export.xml at {LOCAL_XML_PATH}...")
            from src.parser.parser import AppleHealthParser
            
            # Create data directory if it doesn't exist
            os.makedirs(os.path.dirname(LOCAL_DB_PATH), exist_ok=True)
            
            # Parse with default date cutoff (6 months)
            parser = AppleHealthParser(db_path=LOCAL_DB_PATH)
            parser.parse_file(LOCAL_XML_PATH)
            
            print(f"Successfully created local database at {LOCAL_DB_PATH}")
            
        except Exception as e:
            print(f"Error during local parsing: {str(e)}")
            raise

def get_db_connection():
    """Get a connection to the SQLite database."""
    if DATA_REPO and IS_HF_SPACE:
        # Running in HF Spaces - download from private dataset
        try:
            db_path = hf_hub_download(
                repo_id=DATA_REPO,
                filename="health_data.db",
                repo_type="dataset",
                token=HF_TOKEN
            )
            return sqlite3.connect(db_path)
        except Exception as e:
            raise Exception(f"Failed to download database from {DATA_REPO}: {str(e)}")
    else:
        # Local development mode
        if not os.path.exists(LOCAL_DB_PATH):
            raise FileNotFoundError(f"Database file not found: {LOCAL_DB_PATH}. Try restarting the server to trigger auto-parsing.")
        return sqlite3.connect(LOCAL_DB_PATH)

def execute_sql_query(sql_query):
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
        conn = get_db_connection()
        
        # Execute the query
        result = pd.read_sql_query(sql_query, conn)
        conn.close()
        
        # Convert to JSON
        return json.dumps(result.to_dict('records'), indent=2)
        
    except Exception as e:
        return f"Error executing SQL query: {str(e)}"

# Run one-time parsing check when server starts
try:
    check_and_parse_if_needed()
except Exception as e:
    print(f"Warning: Could not check/parse database: {str(e)}")

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
                ["SELECT * FROM activitysummary LIMIT 5;"],
                ["SELECT * FROM healthdata;"],
                ["SELECT date_components, active_energy_burned, apple_exercise_time FROM activitysummary ORDER BY date_components DESC LIMIT 10;"],
                ["SELECT COUNT(*) as count, type FROM record GROUP BY type ORDER BY count DESC LIMIT 10;"],
                ["SELECT * FROM workout LIMIT 5;"],
                ["SELECT type, value, unit, date(start_date) as date FROM record WHERE type LIKE '%HeartRate%' ORDER BY start_date DESC LIMIT 10;"]
            ],
            inputs=sql_input
        )
        
        query_btn.click(
            fn=execute_sql_query,
            inputs=[sql_input],
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