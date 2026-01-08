"""
Google Places API integration for clinic address geocoding.

Uses Places API (New) for geocoding addresses and retrieving place details.
Docs: https://developers.google.com/maps/documentation/places/web-service
"""
import os
import asyncio
import httpx
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
PLACES_API_BASE = "https://places.googleapis.com/v1"


async def geocode_address(
    address: str,
    city: str,
    state: str,
    country: str = "USA",
    zip_code: Optional[str] = None,
    language: str = "en",
    max_retries: int = 3
) -> Dict[str, Any]:
    """
    Geocode a clinic address using Google Places API.

    Args:
        address: Street address
        city: City name
        state: State/province
        country: Country (default USA)
        zip_code: Optional ZIP/postal code
        language: Language code for localized results (en, ru, es)
        max_retries: Number of retries with exponential backoff

    Returns:
        {
            "success": True,
            "lat": 32.0853,
            "lng": 34.7818,
            "place_id": "ChIJ...",
            "google_maps_uri": "https://www.google.com/maps/place/?q=place_id:ChIJ...",
            "formatted_address": "123 Main St, City, State 12345"
        }
    """
    if not GOOGLE_MAPS_API_KEY:
        return {"success": False, "error": "GOOGLE_MAPS_API_KEY not configured"}

    # Build full address string
    address_parts = [address, city, state]
    if zip_code:
        address_parts.append(zip_code)
    address_parts.append(country)
    full_address = ", ".join(filter(None, address_parts))

    # Retry with exponential backoff
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Use Text Search (New) for geocoding with language preference
                response = await client.post(
                    f"{PLACES_API_BASE}/places:searchText",
                    headers={
                        "Content-Type": "application/json",
                        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
                        "X-Goog-FieldMask": "places.id,places.location,places.formattedAddress,places.googleMapsUri"
                    },
                    json={
                        "textQuery": full_address,
                        "maxResultCount": 1,
                        "languageCode": language
                    }
                )

                if response.status_code != 200:
                    error_msg = f"Places API error: {response.status_code}"
                    logger.warning(f"{error_msg} - {response.text}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    return {"success": False, "error": error_msg, "details": response.text}

                data = response.json()
                places = data.get("places", [])

                if not places:
                    # ZERO_RESULTS - log and alert, don't retry
                    logger.warning(f"No geocoding results for address: {full_address}")
                    return {"success": False, "error": "ZERO_RESULTS", "address": full_address}

                place = places[0]
                location = place.get("location", {})

                return {
                    "success": True,
                    "lat": location.get("latitude"),
                    "lng": location.get("longitude"),
                    "place_id": place.get("id"),
                    "google_maps_uri": place.get("googleMapsUri"),
                    "formatted_address": place.get("formattedAddress")
                }

        except httpx.TimeoutException:
            logger.warning(f"Geocoding timeout (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            return {"success": False, "error": "Geocoding request timed out after retries"}
        except Exception as e:
            logger.error(f"Geocoding error: {e}")
            return {"success": False, "error": str(e)}

    return {"success": False, "error": "Max retries exceeded"}


def build_directions_url(
    place_id: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None
) -> str:
    """
    Build a Google Maps Directions URL.

    Uses place_id if available (more stable), falls back to coordinates.
    Docs: https://developers.google.com/maps/documentation/urls/get-started
    """
    if place_id:
        return f"https://www.google.com/maps/dir/?api=1&destination_place_id={place_id}"
    elif lat and lng:
        return f"https://www.google.com/maps/dir/?api=1&destination={lat},{lng}"
    else:
        return ""


def build_location_data(geocode_result: Dict[str, Any]) -> Dict[str, Any]:
    """Build location_data JSONB from geocoding result."""
    if not geocode_result.get("success"):
        return {}

    return {
        "lat": geocode_result.get("lat"),
        "lng": geocode_result.get("lng"),
        "place_id": geocode_result.get("place_id"),
        "google_maps_uri": geocode_result.get("google_maps_uri"),
        "directions_url": build_directions_url(
            place_id=geocode_result.get("place_id"),
            lat=geocode_result.get("lat"),
            lng=geocode_result.get("lng")
        ),
        "formatted_address": geocode_result.get("formatted_address"),
        "geocoded_at": None  # Will be set by caller with timestamp
    }
