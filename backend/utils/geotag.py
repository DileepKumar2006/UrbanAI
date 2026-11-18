import requests
import logging
from datetime import datetime
from typing import Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GeotagService:
    """Handle geolocation and address tagging"""
    
    @staticmethod
    def get_address_from_coordinates(latitude: float, longitude: float) -> str:
        """Convert GPS coordinates to address (using reverse geocoding)"""
        try:
            # Using OpenStreetMap Nominatim (free)
            url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={latitude}&lon={longitude}"
            
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                return data.get('address', {}).get('road', 'Unknown Location')
            
            return "Unknown Location"
            
        except Exception as e:
            logger.error(f"Error getting address: {str(e)}")
            return "Unknown Location"
    
    @staticmethod
    def get_coordinates_from_address(address: str) -> Tuple[float, float]:
        """Convert address to GPS coordinates (geocoding)"""
        try:
            url = f"https://nominatim.openstreetmap.org/search?q={address}&format=json"
            
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data:
                    return (float(data[0]['lat']), float(data[0]['lon']))
            
            return (0.0, 0.0)
            
        except Exception as e:
            logger.error(f"Error getting coordinates: {str(e)}")
            return (0.0, 0.0)


if __name__ == '__main__':
    geo_service = GeotagService()
    address = geo_service.get_address_from_coordinates(0.0, 0.0)
    print(f"Geotag service test address: {address}")
