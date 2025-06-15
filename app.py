import gradio as gr
from huggingface_hub import HfApi, create_repo
from huggingface_hub.utils import RepositoryNotFoundError
import os
import tempfile
from src.parser.parser import AppleHealthParser


def create_interface():
    """Create the Gradio interface with OAuth login for Apple Health Landing Zone."""
    
    with gr.Blocks(title="Apple Health Landing Zone") as demo:
        gr.Markdown("# Apple Health Landing Zone")
        gr.Markdown("Login with your Hugging Face account to create a private dataset for your Apple Health export and an MCP server space.")
        
        # OAuth login
        gr.LoginButton()
        
        # User info display
        user_info = gr.Markdown("")
        
        # Upload section (initially hidden)
        with gr.Column(visible=False) as upload_section:
            gr.Markdown("### Upload Apple Health Export")
            gr.Markdown("Upload your export.xml file from Apple Health. This will create:")
            gr.Markdown("1. A private dataset to store your health data")
            gr.Markdown("2. A private space with an MCP server to query your data")
            
            file_input = gr.File(
                label="Apple Health export.xml",
                file_types=[".xml"],
                type="filepath"
            )
            
            space_name_input = gr.Textbox(
                label="Project Name",
                placeholder="my-health-data",
                info="Enter a name for your health data project (lowercase, no spaces)"
            )
            
            create_btn = gr.Button("Create Landing Zone", variant="primary")
            create_status = gr.Markdown("")
        
        def create_health_landing_zone(file_path: str, project_name: str, oauth_token: gr.OAuthToken | None) -> str:
            """Create private dataset and MCP server space for Apple Health data."""
            if not oauth_token:
                return "❌ Please login first!"
            
            if not file_path:
                return "❌ Please upload your export.xml file!"
            
            if not project_name:
                return "❌ Please enter a project name!"
            
            try:
                # Use the OAuth token
                token = oauth_token.token
                if not token:
                    return "❌ No access token found. Please login again."
                    
                api = HfApi(token=token)
                
                # Get the current user's username
                user_info = api.whoami()
                username = user_info["name"]
                
                # Create dataset repository
                dataset_repo_id = f"{username}/{project_name}-data"
                space_repo_id = f"{username}/{project_name}-mcp"
                
                # Check if repositories already exist
                try:
                    api.repo_info(dataset_repo_id, repo_type="dataset")
                    return f"❌ Dataset '{dataset_repo_id}' already exists!"
                except RepositoryNotFoundError:
                    pass
                
                try:
                    api.repo_info(space_repo_id, repo_type="space")
                    return f"❌ Space '{space_repo_id}' already exists!"
                except RepositoryNotFoundError:
                    pass
                
                # Create the private dataset
                dataset_url = create_repo(
                    repo_id=dataset_repo_id,
                    repo_type="dataset",
                    private=True,
                    token=token
                )
                
                # Upload the export.xml file (first commit)
                api.upload_file(
                    path_or_fileobj=file_path,
                    path_in_repo="export.xml",
                    repo_id=dataset_repo_id,
                    repo_type="dataset",
                    token=token,
                    commit_message="Initial upload: export.xml"
                )
                
                # Parse the XML file and create SQLite database
                with tempfile.TemporaryDirectory() as temp_dir:
                    db_path = os.path.join(temp_dir, "health_data.db")
                    
                    # Parse the XML file
                    parser = AppleHealthParser(db_path=db_path)
                    parser.parse_file(file_path)
                    
                    # Upload the SQLite database (second commit)
                    api.upload_file(
                        path_or_fileobj=db_path,
                        path_in_repo="health_data.db",
                        repo_id=dataset_repo_id,
                        repo_type="dataset",
                        token=token,
                        commit_message="Add parsed SQLite database"
                    )
                
                # Create README for dataset
                dataset_readme = f"""# Apple Health Data

This is a private dataset containing Apple Health export data for {username}.

## Files
- `export.xml`: The original Apple Health export file
- `health_data.db`: SQLite database with parsed health data

## Associated MCP Server
- Space: [{space_repo_id}](https://huggingface.co/spaces/{space_repo_id})

## Privacy
This dataset is private and contains personal health information. Do not share access with others.
"""
                
                api.upload_file(
                    path_or_fileobj=dataset_readme.encode(),
                    path_in_repo="README.md",
                    repo_id=dataset_repo_id,
                    repo_type="dataset",
                    token=token
                )
                
                # Create the MCP server space
                space_url = create_repo(
                    repo_id=space_repo_id,
                    repo_type="space",
                    space_sdk="gradio",
                    private=True,
                    token=token
                )
                
                # Create MCP server app.py
                mcp_app_content = f'''import gradio as gr
from huggingface_hub import hf_hub_download
import sqlite3
import pandas as pd
from datetime import datetime
import json

# Download the health data
DATA_REPO = "{dataset_repo_id}"

def get_db_connection():
    """Get a connection to the SQLite database."""
    # Download the health_data.db file from the dataset
    db_path = hf_hub_download(
        repo_id=DATA_REPO,
        filename="health_data.db",
        repo_type="dataset",
        use_auth_token=True
    )
    return sqlite3.connect(db_path)

def execute_sql_query(sql_query):
    """Execute any SQL query on the Apple Health SQLite database.
    
    Args:
        sql_query (str): The SQL query to execute
        
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
        return f"Error executing SQL query: {{str(e)}}"

# MCP Server Interface
with gr.Blocks(title="Apple Health MCP Server") as demo:
    gr.Markdown("# Apple Health MCP Server")
    gr.Markdown(f"This is an MCP server for querying Apple Health data from dataset: `{{DATA_REPO}}`")
    
    with gr.Tab("SQL Query Interface"):
        gr.Markdown("### Execute SQL Queries")
        gr.Markdown("Enter any SQL query to execute against your Apple Health SQLite database.")
        
        sql_input = gr.Textbox(
            label="SQL Query",
            placeholder="SELECT * FROM records LIMIT 10;",
            lines=5,
            info="Enter your SQL query here. Available tables: records, workouts"
        )
        
        query_btn = gr.Button("Execute Query", variant="primary")
        output = gr.Code(language="json", label="Query Results")
        
        query_btn.click(
            fn=execute_sql_query,
            inputs=[sql_input],
            outputs=output
        )
    
    with gr.Tab("MCP Endpoint"):
        gr.Markdown("""
        ## MCP Server Endpoint
        
        This space can be used as an MCP server with the following configuration:
        
        ```json
        {{
            "mcpServers": {{
                "apple-health": {{
                    "command": "npx",
                    "args": [
                        "-y",
                        "@modelcontextprotocol/server-huggingface",
                        "{space_repo_id}",
                        "--use-auth"
                    ]
                }}
            }}
        }}
        ```
        
        Add this to your Claude Desktop configuration to query your Apple Health data through Claude.
        """)

if __name__ == "__main__":
    demo.launch(mcp_server=True)
'''
                
                api.upload_file(
                    path_or_fileobj=mcp_app_content.encode(),
                    path_in_repo="app.py",
                    repo_id=space_repo_id,
                    repo_type="space",
                    token=token
                )
                
                # Create requirements.txt for the space
                requirements_content = """gradio>=5.34.0
huggingface-hub>=0.20.0
pandas>=2.0.0
"""
                
                api.upload_file(
                    path_or_fileobj=requirements_content.encode(),
                    path_in_repo="requirements.txt",
                    repo_id=space_repo_id,
                    repo_type="space",
                    token=token
                )
                
                return f"""✅ Successfully created Apple Health Landing Zone!

**Private Dataset:** [{dataset_repo_id}]({dataset_url})
- Your export.xml file has been securely uploaded
- SQLite database (health_data.db) has been generated from your data

**MCP Server Space:** [{space_repo_id}]({space_url})
- Query interface for your health data using SQLite
- MCP endpoint configuration included

Both repositories are private and only accessible by you."""
                
            except Exception as e:
                return f"❌ Error creating landing zone: {str(e)}"
        
        def update_ui(profile: gr.OAuthProfile | None) -> tuple:
            """Update UI based on login status."""
            if profile:
                username = profile.username
                return (
                    f"✅ Logged in as **{username}**",
                    gr.update(visible=True)
                )
            else:
                return (
                    "",
                    gr.update(visible=False)
                )
        
        # Update UI when login state changes
        demo.load(update_ui, inputs=None, outputs=[user_info, upload_section])
        
        # Create landing zone button click
        create_btn.click(
            fn=create_health_landing_zone,
            inputs=[file_input, space_name_input],
            outputs=[create_status]
        )
    
    return demo


def main():
    # Check if running in Hugging Face Spaces
    if os.getenv("SPACE_ID"):
        # Running in Spaces, launch with appropriate settings
        demo = create_interface()
        demo.launch()
    else:
        # Running locally, note that OAuth won't work
        print("Note: OAuth login only works when deployed to Hugging Face Spaces.")
        print("To test locally, deploy this as a Space with hf_oauth: true in README.md")
        demo = create_interface()
        demo.launch()


if __name__ == "__main__":
    main()