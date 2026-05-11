import os
import glob

def add_security(file_path):
    with open(file_path, "r") as f:
        content = f.read()

    if "from app.security import verify_api_key" not in content:
        content = content.replace("from fastapi import APIRouter", "from fastapi import APIRouter, Depends\nfrom app.security import verify_api_key")

    if "dependencies=[Depends(verify_api_key)]" not in content:
        content = content.replace("APIRouter()", "APIRouter(dependencies=[Depends(verify_api_key)])")

    with open(file_path, "w") as f:
        f.write(content)

for d in glob.glob("cartridges/*/app/api/*.py"):
    if "routes_skills.py" in d:
        add_security(d)

for d in glob.glob("cartridges/*/app/mcp_server.py"):
    with open(d, "r") as f:
        content = f.read()

    if "from app.security import verify_api_key" not in content:
        content = content.replace("from mcp.server.fastmcp import FastMCP", "from mcp.server.fastmcp import FastMCP\nfrom fastapi import Depends\nfrom app.security import verify_api_key")

    if "dependencies=[Depends(verify_api_key)]" not in content:
        content = content.replace("FastMCP(name", "FastMCP(name, dependencies=[Depends(verify_api_key)]")

    with open(d, "w") as f:
        f.write(content)
