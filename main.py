from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import requests

app = FastAPI(title="Real Street Heat Risk API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"

HEADERS = {"User-Agent": "AI-Heat-Risk-Demo/1.0"}


def fetch_place_name(lat, lon):
    try:
        params = {
            "format": "json",
            "lat": lat,
            "lon": lon,
            "zoom": 18,
            "addressdetails": 1,
            "accept-language": "en",
        }
        r = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get("display_name", "Unknown location")
    except Exception:
        return "Unknown location"


def fetch_weather(lat, lon):
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,relative_humidity_2m,apparent_temperature",
        "forecast_days": 1,
        "timezone": "auto",
    }

    r = requests.get(WEATHER_URL, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()

    return (
        data["hourly"]["time"],
        data["hourly"]["temperature_2m"],
        data["hourly"]["relative_humidity_2m"],
        data["hourly"]["apparent_temperature"],
    )


def get_surface_score(surface):
    if surface == "Road / Concrete":
        return 30
    if surface == "Mixed Area":
        return 18
    if surface == "Park / Trees":
        return 6
    return 15


def highway_to_surface(highway):
    if highway in ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified"]:
        return "Road / Concrete"
    if highway in ["residential", "service", "living_street", "pedestrian", "footway", "cycleway"]:
        return "Mixed Area"
    return "Park / Trees"


def calculate_risk(temp, humidity, apparent_temp, surface_score):
    temp_score = max(0, (temp - 24) * 3)
    humidity_score = max(0, (humidity - 55) * 0.35)
    feels_like_score = max(0, (apparent_temp - 27) * 2)

    risk_score = temp_score + humidity_score + feels_like_score + surface_score
    risk_score = max(0, min(100, int(risk_score)))

    if risk_score >= 70:
        return risk_score, "DANGER", "Avoid outdoor exposure and use shaded routes."
    if risk_score >= 40:
        return risk_score, "ALERT", "Take precautions, stay hydrated, and avoid long exposure."
    return risk_score, "SAFE", "Area is safe for outdoor activity."


def fallback_streets(lat, lon):
    return [
        {"name": "Main Concrete Road", "lat": lat + 0.004, "lon": lon + 0.004, "surface": "Road / Concrete", "highway_type": "primary", "is_real_osm": False},
        {"name": "Busy Market Street", "lat": lat - 0.004, "lon": lon + 0.004, "surface": "Road / Concrete", "highway_type": "secondary", "is_real_osm": False},
        {"name": "School Mixed Zone", "lat": lat + 0.004, "lon": lon - 0.004, "surface": "Mixed Area", "highway_type": "residential", "is_real_osm": False},
        {"name": "Green Park Road", "lat": lat - 0.004, "lon": lon - 0.004, "surface": "Park / Trees", "highway_type": "park_path", "is_real_osm": False},
        {"name": "Hospital Access Road", "lat": lat + 0.006, "lon": lon - 0.0025, "surface": "Mixed Area", "highway_type": "service", "is_real_osm": False},
        {"name": "Industrial Concrete Lane", "lat": lat - 0.006, "lon": lon + 0.0025, "surface": "Road / Concrete", "highway_type": "industrial", "is_real_osm": False},
    ]


@app.get("/")
def home():
    return FileResponse("frontend.html")


@app.get("/app")
def mobile_app():
    return FileResponse("frontend.html")


@app.get("/place")
def get_place(lat: float = Query(...), lon: float = Query(...)):
    return {
        "latitude": lat,
        "longitude": lon,
        "place_name": fetch_place_name(lat, lon),
    }


@app.get("/risk")
def get_risk(
    lat: float = Query(...),
    lon: float = Query(...),
    surface: str = Query("Road / Concrete"),
):
    try:
        surface_score = get_surface_score(surface)
        times, temps, humidity, apparent_temps = fetch_weather(lat, lon)

        outputs = []

        for label, index in [("Now", 0), ("+2 Hours", 2), ("+6 Hours", 6)]:
            risk_score, level, advice = calculate_risk(
                temps[index],
                humidity[index],
                apparent_temps[index],
                surface_score,
            )

            outputs.append({
                "forecast": label,
                "time": times[index],
                "temperature": temps[index],
                "humidity": humidity[index],
                "apparent_temperature": apparent_temps[index],
                "surface_score": surface_score,
                "risk_score": risk_score,
                "level": level,
                "advice": advice,
            })

        return {
            "location": {"latitude": lat, "longitude": lon},
            "surface": surface,
            "results": outputs,
        }

    except Exception as e:
        return {
            "error": str(e),
            "location": {"latitude": lat, "longitude": lon},
            "surface": surface,
            "results": [],
        }


@app.get("/streets")
def get_streets(
    lat: float = Query(...),
    lon: float = Query(...),
    radius: int = Query(900),
):
    try:
        query = f"""
        [out:json][timeout:20];
        way["highway"](around:{radius},{lat},{lon});
        out center tags;
        """

        r = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers=HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()

        streets = []
        seen = set()

        for element in data.get("elements", []):
            try:
                tags = element.get("tags", {})
                center = element.get("center")

                if not center:
                    continue

                highway = tags.get("highway", "road")
                name = tags.get("name:en") or tags.get("name") or f"Unnamed {highway}"

                key = f"{name}-{round(center['lat'], 5)}-{round(center['lon'], 5)}"
                if key in seen:
                    continue

                seen.add(key)

                streets.append({
                    "name": name,
                    "lat": center["lat"],
                    "lon": center["lon"],
                    "surface": highway_to_surface(highway),
                    "highway_type": highway,
                    "is_real_osm": True,
                })

            except Exception:
                continue

        if len(streets) == 0:
            streets = fallback_streets(lat, lon)
            source = "Fallback demo streets"
        else:
            source = "OpenStreetMap Overpass API"

        return {
            "source": source,
            "count": len(streets[:12]),
            "streets": streets[:12],
        }

    except Exception:
        streets = fallback_streets(lat, lon)
        return {
            "source": "Fallback demo streets",
            "count": len(streets),
            "streets": streets,
        }