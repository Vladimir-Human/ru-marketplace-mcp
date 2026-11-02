docker build -t ru-marketplace-mcp:1.3.0 .
python -m pip install --no-deps wheelhouse/mcp_core-1.3.0-py3-none-any.whl
python -m pip install --find-links wheelhouse "compare-connector[all]==1.3.0" "aliexpress-connector==1.3.0"
