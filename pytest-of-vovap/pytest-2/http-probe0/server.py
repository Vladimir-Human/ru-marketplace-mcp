
from fastmcp import Context, FastMCP
from mcp_core.runtime import current_client_scope, run_server

mcp = FastMCP("http-probe")

@mcp.tool
def scope(ctx: Context) -> dict:
    return {"scope": current_client_scope(ctx), "transport": ctx.transport}

raise SystemExit(run_server(mcp, server_name="http-probe"))
