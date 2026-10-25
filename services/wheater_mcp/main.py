
import httpx
from mcp.server.fastmcp import FastMCP

app = FastMCP(
    "Weather MCP Server",
    host="0.0.0.0",
    port=8002
)


@app.tool()
async def get_current_weather(location: str) -> dict:
    """Get current weather for a city such as Trier or Berlin."""

    async with httpx.AsyncClient(timeout=10.0) as client:


        response = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={
                "name": location,
                "count": 1
            }
        )
        response.raise_for_status()

        places = response.json().get("results", [])

        if not places:
            return {"error": f"City not found: {location}"}

        place = places[0]

    
        weather_response = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": (
                    "temperature_2m,"
                    "relative_humidity_2m,"
                    "weather_code,"
                    "wind_speed_10m"
                )
            }
        )
        weather_response.raise_for_status()

        current = weather_response.json()["current"]


        return {
            "location": place["name"],
            "country": place.get("country"),
            "temperature_c": current.get("temperature_2m"),
            "humidity_percent": current.get("relative_humidity_2m"),
            "weather_code": current.get("weather_code"),
            "wind_speed_kmh": current.get("wind_speed_10m")
        }


if __name__ == "__main__":
    app.run(transport="streamable-http")
