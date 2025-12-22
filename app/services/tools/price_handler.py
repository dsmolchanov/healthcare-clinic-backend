from typing import Any, Dict
import logging
from app.services.tools.base import ToolHandler
from app.tools.price_query_tool import PriceQueryTool
from app.config import get_redis_client

logger = logging.getLogger(__name__)

class PriceQueryHandler(ToolHandler):
    @property
    def tool_name(self) -> str:
        return "query_service_prices"

    async def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> str:
        clinic_id = context.get('clinic_id')
        if not clinic_id:
            return "Error: clinic_id missing from context"

        redis_client = get_redis_client()
        price_tool = PriceQueryTool(clinic_id=clinic_id, redis_client=redis_client)
        services = await price_tool.get_services_by_query(**args)

        logger.info(f"[PriceQueryHandler] Query: {args}, Found {len(services) if services else 0} services")

        if services:
            result_text = "Found services:\n"
            for svc in services[:5]:
                # Handle both base_price and price field names
                price_value = svc.get('price') or svc.get('base_price')
                price = f"${price_value:.2f}" if price_value else "Price on request"
                result_text += f"- {svc['name']}: {price}\n"
            logger.info(f"[PriceQueryHandler] Result: {result_text[:200]}...")
        else:
            result_text = "No services found matching your query."
            logger.warning(f"[PriceQueryHandler] No services found for query: {args}")

        return result_text
