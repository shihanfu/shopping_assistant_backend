# shopping_assistant/tool_config.py

TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "search",
                "description": "Search for products by query string.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search term"
                            }
                        },
                        "required": ["query"]
                    }
                }
            }
        },
        {
            "toolSpec": {
                "name": "visit_product",
                "description": "Visit a product page by its URL.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "product_url": {
                                "type": "string",
                                "description": "Product page URL"
                            }
                        },
                        "required": ["product_url"]
                    }
                }
            }
        }
    ]
}
