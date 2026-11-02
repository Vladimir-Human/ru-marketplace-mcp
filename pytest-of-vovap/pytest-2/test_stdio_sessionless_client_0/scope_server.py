
from fastmcp import Context, FastMCP
from mcp_core.runtime import current_client_scope

mcp = FastMCP("scope-probe")

@mcp.tool
def scope(ctx: Context) -> dict:
    return {"scope": current_client_scope(ctx), "session_id": ctx.session_id}

if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
