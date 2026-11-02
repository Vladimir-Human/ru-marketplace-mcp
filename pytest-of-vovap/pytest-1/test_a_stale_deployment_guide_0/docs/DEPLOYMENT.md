docker build -t ru-marketplace-mcp:1.2.3 .
python -m pip install --no-deps wheelhouse/mcp_core-1.2.2-py3-none-any.whl
python -m pip install --find-links wheelhouse "compare-connector[all]==1.2.3" "aliexpress-connector==1.2.3"
