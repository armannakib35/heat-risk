from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import requests
import logging

app = FastAPI(title="Real Street Heat Risk API")

# Enable CORS so frontend can call APIs from other devices
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {"User-Agent": "AI-Heat-Risk-Demo/1.0"}


def fetch_place_name(lat, lon):
    params = {
        "format": "json",
        "lat": lat,
        "lon": lon,
        "zoom": 18,
        "addressdetails": 1,
        "accept-language": "en"
    }
    try:
        r = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("display_name", "Unknown location")
    except Exception as e:
        logging.error(f"Place fetch error: {e}")
        return "Unknown location"


def fetch_weather(lat, lon):
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,relative_humidity_2m,apparent_temperature",
        "forecast_days": 1,
        "timezone": "auto",
    }
    r = requests.get(WEATHER_URL, params=params, timeout=15)
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
    concrete_types = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified"]
    mixed_types = ["residential", "service", "living_street", "pedestrian", "footway", "cycleway"]
    if highway in concrete_types:
        return "Road / Concrete"
    if highway in mixed_types:
        return "Mixed Area"
    return "Park / Trees"


def calculate_risk(temp, humidity, apparent_temp, surface_score):
    temp_score = max(0, (temp - 24) * 3)
    humidity_score = max(0, (humidity - 55) * 0.35)
    feels_like_score = max(0, (apparent_temp - 27) * 2)
    risk_score = temp_score + humidity_score + feels_like_score + surface_score
    risk_score = max(0, min(100, int(risk_score)))

    if risk_score >= 70:
        level = "DANGER"
        advice = "Avoid outdoor exposure and use shaded routes."
    elif risk_score >= 40:
        level = "ALERT"
        advice = "Take precautions, stay hydrated, and avoid long exposure."
    else:
        level = "SAFE"
        advice = "Area is safe for outdoor activity."
    return risk_score, level, advice


@app.get("/")
def home():
    return {"message": "Street Heat Risk API is running"}


@app.get("/app")
def mobile_app():
    return FileResponse("frontend.html")


@app.get("/place")
def get_place(lat: float = Query(...), lon: float = Query(...)):
    place_name = fetch_place_name(lat, lon)
    return {"latitude": lat, "longitude": lon, "place_name": place_name}


@app.get("/streets")
def get_streets(lat: float = Query(...), lon: float = Query(...), radius: int = Query(700)):
    query = f"""
    [out:json][timeout:25];
    way["highway"](around:{radius},{lat},{lon});
    out center tags;
    """
    try:
        r = requests.post(OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logging.error(f"Overpass API error: {e}")
        return {"error": str(e), "streets": []}

    streets = []
    seen = set()
    for element in data.get("elements", []):
        tags = element.get("tags", {})
        center = element.get("center")
        if not center:
            continue
        highway = tags.get("highway", "road")
        name = tags.get("name:en") or tags.get("name") or f"Unnamed {highway} road"
        key = f"{name}-{round(center['lat'],5)}-{round(center['lon'],5)}"
        if key in seen:
            continue
        seen.add(key)
        surface = highway_to_surface(highway)
        streets.append({
            "name": name,
            "lat": center["lat"],
            "lon": center["lon"],
            "surface": surface,
            "highway_type": highway,
            "is_real_osm": True
        })
    return {"source": "OpenStreetMap Overpass API", "count": len(streets[:20]), "streets": streets[:20]}


@app.get("/risk")
def get_risk(lat: float = Query(...), lon: float = Query(...), surface: str = Query("Road / Concrete")):
    surface_score = get_surface_score(surface)
    times, temps, humidity, apparent_temps = fetch_weather(lat, lon)

    outputs = []

    # 0 to 6 hours, 30-min steps
    for step in range(13):
        lower_index = int(step * 0.5)
        upper_index = min(lower_index + 1, len(temps) - 1)
        fraction = (step * 0.5) - lower_index

        temp = temps[lower_index] + (temps[upper_index] - temps[lower_index]) * fraction
        hum = humidity[lower_index] + (humidity[upper_index] - humidity[lower_index]) * fraction
        apparent_temp = apparent_temps[lower_index] + (apparent_temps[upper_index] - apparent_temps[lower_index]) * fraction

        risk_score, level, advice = calculate_risk(temp, hum, apparent_temp, surface_score)

        # Custom warnings for user
        if level == "DANGER":
            warning = "Drink water, use sunscreen, stay in shade, avoid going out if possible."
        elif level == "ALERT":
            warning = "Drink water, carry umbrella, limit sun exposure."
        else:
            warning = "Low risk. Stay hydrated, carry water."

        outputs.append({
            "forecast": f"+{step*30} min",
            "time": times[lower_index],
            "temperature": round(temp,1),
            "humidity": round(hum,1),
            "apparent_temperature": round(apparent_temp,1),
            "surface_score": surface_score,
            "risk_score": risk_score,
            "level": level,
            "advice": advice,
            "warning": warning
        })

    outputs[0]["forecast"] = "Now"

    return {
        "location": {"latitude": lat, "longitude": lon},
        "surface": surface,
        "results": outputs
    }
