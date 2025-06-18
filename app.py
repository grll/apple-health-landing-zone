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
            
            create_btn = gr.Button("Create Private MCP Server", variant="primary")
            create_status = gr.Markdown("")
            progress_bar = gr.Markdown(visible=False)
        
        def create_health_landing_zone(file_path: str, project_name: str, oauth_token: gr.OAuthToken | None, progress=gr.Progress()) -> str:
            """Create private dataset and MCP server space for Apple Health data."""
            if not oauth_token:
                return "❌ Please login first!"
            
            if not file_path:
                return "❌ Please upload your export.xml file!"
            
            if not project_name:
                return "❌ Please enter a project name!"
            
            try:
                progress(0.1, desc="🔐 Authenticating...")
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
                
                progress(0.2, desc="🔍 Checking repository availability...")
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
                
                progress(0.3, desc="📊 Creating private dataset...")
                # Create the private dataset
                dataset_url = create_repo(
                    repo_id=dataset_repo_id,
                    repo_type="dataset",
                    private=True,
                    token=token
                )
                
                progress(0.4, desc="📤 Uploading export.xml...")
                # Upload the export.xml file (first commit)
                api.upload_file(
                    path_or_fileobj=file_path,
                    path_in_repo="export.xml",
                    repo_id=dataset_repo_id,
                    repo_type="dataset",
                    token=token,
                    commit_message="Initial upload: export.xml"
                )
                
                progress(0.5, desc="📝 Creating dataset documentation...")
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
                
                progress(0.6, desc="🚀 Creating MCP server space...")
                # Create the MCP server space
                space_url = create_repo(
                    repo_id=space_repo_id,
                    repo_type="space",
                    space_sdk="gradio",
                    private=True,
                    token=token
                )
                
                progress(0.7, desc="📦 Uploading MCP server code...")
                # Read MCP server code from mcp_server.py 
                with open('mcp_server.py', 'r') as f:
                    mcp_app_content = f.read()
                
                # Upload the MCP server app.py
                api.upload_file(
                    path_or_fileobj=mcp_app_content.encode(),
                    path_in_repo="app.py",
                    repo_id=space_repo_id,
                    repo_type="space",
                    token=token
                )
                
                progress(0.8, desc="📚 Uploading parser dependencies...")
                # Upload parser dependencies for auto-parsing functionality
                api.upload_file(
                    path_or_fileobj="src/parser/parser.py",
                    path_in_repo="src/parser/parser.py",
                    repo_id=space_repo_id,
                    repo_type="space",
                    token=token
                )
                
                api.upload_file(
                    path_or_fileobj="src/parser/models.py",
                    path_in_repo="src/parser/models.py",
                    repo_id=space_repo_id,
                    repo_type="space",
                    token=token
                )
                
                api.upload_file(
                    path_or_fileobj="src/parser/__init__.py",
                    path_in_repo="src/parser/__init__.py",
                    repo_id=space_repo_id,
                    repo_type="space",
                    token=token
                )
                
                api.upload_file(
                    path_or_fileobj="src/__init__.py",
                    path_in_repo="src/__init__.py",
                    repo_id=space_repo_id,
                    repo_type="space",
                    token=token
                )
                
                progress(0.85, desc="📋 Creating requirements...")
                # Create requirements.txt for the space
                requirements_content = """gradio>=5.34.0
huggingface-hub>=0.20.0
pandas>=2.0.0
lxml>=4.9.0
sqlmodel>=0.0.8
tqdm>=4.64.0
"""
                
                api.upload_file(
                    path_or_fileobj=requirements_content.encode(),
                    path_in_repo="requirements.txt",
                    repo_id=space_repo_id,
                    repo_type="space",
                    token=token
                )
                
                progress(0.9, desc="🔧 Configuring environment variables...")
                # Create space variables for the dataset repo ID
                api.add_space_variable(
                    repo_id=space_repo_id,
                    key="DATA_REPO",
                    value=dataset_repo_id,
                    token=token
                )
                
                progress(0.95, desc="🔐 Setting up secure access...")
                # Add the token as a secret for dataset access
                api.add_space_secret(
                    repo_id=space_repo_id,
                    key="HF_TOKEN",
                    value=token,
                    token=token
                )
                
                progress(1.0, desc="✅ Complete!")
                return f"""✅ Successfully created your Private Apple Health Dataset and MCP Server!

**Private Dataset:** [{dataset_repo_id}]({dataset_url})
- Your export.xml file has been securely uploaded.
- A SQLite database (health_data.db) will be generated from your data as soon as the MCP Server Space starts.
- Note: it might take several minutes (up to 1 hour or more) depeding on the size of your export.xml file.

**MCP Server Space:** [{space_repo_id}]({space_url})
- Query interface for your health data using SQLite
- MCP endpoint configuration included
- Environment variables automatically configured
- It will work until expiration of your oauth token, to make it work permanently, create a new fine-grained token with `read` and `write` permissions to your private health dataset and set it as `HF_TOKEN` secret in the MCP Server Space.

Both repositories are private and only accessible by you.
"""
                
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
            fn=lambda: gr.update(interactive=False),
            outputs=[create_btn]
        ).then(
            fn=create_health_landing_zone,
            inputs=[file_input, space_name_input],
            outputs=[create_status]
        ).then(
            fn=lambda: gr.update(interactive=True),
            outputs=[create_btn]
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