---
title: Apple Health Landing Zone
emoji: 🏥
colorFrom: green
colorTo: blue
sdk: gradio
sdk_version: 5.34.0
app_file: app.py
pinned: false
hf_oauth: true
hf_oauth_scopes:
  - read-repos
  - write-repos
  - manage-repos
---

# Apple Health Landing Zone

Upload your Apple Health export.xml file to create a private data ecosystem on Hugging Face.

## What This Does

This space helps you create:

1. **Private Dataset**: Securely stores your Apple Health export.xml file
2. **MCP Server Space**: A private Gradio space that acts as an MCP (Model Context Protocol) server for querying your health data

## Features

- 🔐 OAuth login with your Hugging Face account
- 📊 Private dataset creation for your health data
- 🖥️ Automatic MCP server setup
- 🔍 Query interface for your health data
- 🤖 Integration with Claude Desktop via MCP

## How to Use

1. Click "Sign in with Hugging Face"
2. Upload your Apple Health export.xml file
3. Choose a project name
4. Click "Create Landing Zone"

## Getting Your Apple Health Data

1. Open the Health app on your iPhone
2. Tap your profile picture in the top right
3. Scroll down and tap "Export All Health Data"
4. Choose to export and save the file
5. Upload the export.xml file here

## Privacy

- All created repositories are **private** by default
- Your health data is only accessible by you
- The MCP server runs in your own private space

## MCP Integration

After creating your landing zone, you can add the MCP server to Claude Desktop by adding the configuration shown in your created space to your Claude Desktop settings.