from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import requests

app = FastAPI(title="Real Street Heat Risk API")

# ---- CORS: Allow all origins for Render deployment ----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {"User-Agent": "AI-Heat-Risk-Demo/1.0"}

# --- Fetch English place name ---
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
        r = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get("display_name", "Unknown location")
    except Exception:
        return "Unknown location"

# --- Weather fetch ---
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

# --- Surface score ---
def get_surface_score(surface):
    if surface == "Road / Concrete":
        return 30
    if surface == "Mixed Area":
        return 18
    if surface == "Park / Trees":
        return 6
    return 15

# --- Highway type to surface class ---
def highway_to_surface(highway):
    concrete_types = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified"]
    mixed_types = ["residential", "service", "living_street", "pedestrian", "footway", "cycleway"]
    if highway in concrete_types:
        return "Road / Concrete"
    if highway in mixed_types:
        return "Mixed Area"
    return "Park / Trees"

# --- Risk calculation ---
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

# --- Routes ---
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

@app.get("/risk")
def get_risk(lat: float = Query(...), lon: float = Query(...), surface: str = Query("Road / Concrete")):
    surface_score = get_surface_score(surface)
    times, temps, humidity, apparent_temps = fetch_weather(lat, lon)
    outputs = []
    for label, index in [("Now", 0), ("+2 Hours", 2), ("+6 Hours", 6)]:
        temp = temps[index]
        hum = humidity[index]
        apparent_temp = apparent_temps[index]
        risk_score, level, advice = calculate_risk(temp, hum, apparent_temp, surface_score)
        outputs.append({
            "forecast": label,
            "time": times[index],
            "temperature": temp,
            "humidity": hum,
            "apparent_temperature": apparent_temp,
            "surface_score": surface_score,
            "risk_score": risk_score,
            "level": level,
            "advice": advice
        })
    return {"location":{"latitude":lat,"longitude":lon},"surface":surface,"results":outputs}

@app.get("/streets")
def get_streets(lat: float = Query(...), lon: float = Query(...), radius: int = Query(700)):
    """
    Fetch nearby streets from OSM; if Overpass fails, use fallback hardcoded streets
    """
    streets = []
    try:
        query = f"""
        [out:json][timeout:25];
        way["highway"](around:{radius},{lat},{lon});
        out center tags;
        """
        r = requests.post(OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        seen = set()
        for el in data.get("elements", []):
            tags = el.get("tags", {})
            center = el.get("center")
            if not center:
                continue
            name = tags.get("name:en") or tags.get("name") or f"Unnamed {tags.get('highway','road')}"
            key = f"{name}-{round(center['lat'],5)}-{round(center['lon'],5)}"
            if key in seen:
                continue
            seen.add(key)
            streets.append({
                "name": name,
                "lat": center["lat"],
                "lon": center["lon"],
                "surface": highway_to_surface(tags.get("highway","road")),
                "highway_type": tags.get("highway","road"),
                "is_real_osm": True
            })
    except:
        # fallback hardcoded streets if Overpass fails
        streets = [
            {"name":"Main Concrete Road","lat":lat+0.004,"lon":lon+0.004,"surface":"Road / Concrete","highway_type":"primary","is_real_osm":False},
            {"name":"Busy Market Street","lat":lat-0.004,"lon":lon+0.004,"surface":"Road / Concrete","highway_type":"secondary","is_real_osm":False},
            {"name":"School Mixed Zone","lat":lat+0.004,"lon":lon-0.004,"surface":"Mixed Area","highway_type":"residential","is_real_osm":False},
            {"name":"Green Park Road","lat":lat-0.004,"lon":lon-0.004,"surface":"Park / Trees","highway_type":"park_path","is_real_osm":False},
            {"name":"Hospital Access Road","lat":lat+0.006,"lon":lon-0.0025,"surface":"Mixed Area","highway_type":"service","is_real_osm":False},
            {"name":"Industrial Concrete Lane","lat":lat-0.006,"lon":lon+0.0025,"surface":"Road / Concrete","highway_type":"industrial","is_real_osm":False}
        ]
    return {"source":"OpenStreetMap Overpass API","count":len(streets[:20]),"streets":streets[:20]}