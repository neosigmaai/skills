# Catalog tool-use mini benchmark (fixture source)

The harness starts the `catalog` MCP server for the model with
`python3 /opt/tools/catalog_mcp.py` (stdio transport) inside the task image.
The model must use it to find the price of SKU `A-17` and write the price to
`/app/price.txt`. `grade.py` compares the file with the expected price and
prints `{"correct": 0 | 1}`. The server needs no credentials or network.
