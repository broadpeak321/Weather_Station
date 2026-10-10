"""
Desktop Weather Station for Windows
-----------------------------------
A small always-on-top Tkinter weather widget with:
- IP-based location lookup (ipapi.co)
- Current conditions, hourly forecast, 7-day forecast
- Temperature, feels-like, humidity, dew point, wind, gusts, pressure,
  visibility, UV index, precipitation, sunrise and sunset
- Right-click menu: themes, size, opacity, refresh, location, startup, close
- Saves preferences and can add itself to the current user's Windows Startup folder

Requirements: Python 3.10+ (no pip packages required)
Weather data: Open-Meteo (https://open-meteo.com/)
IP geolocation: https://ipapi.co/
"""

import json
import os
import sys
import threading
import urllib.parse
import urllib.request
import tkinter as tk
from tkinter import messagebox, simpledialog
from pathlib import Path
from datetime import datetime

APP_NAME = "Desktop Weather Station"
APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "DesktopWeatherStation"
CONFIG_FILE = APP_DIR / "settings.json"
REFRESH_OPTIONS = [5, 10, 15, 30, 60]


def http_json(url, timeout=12):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DesktopWeatherStation/1.1",
            "Accept": "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Include the service and status in the message so network/API failures
        # can be diagnosed without guessing which request was refused.
        raise RuntimeError(
            f"{exc.code} {exc.reason} from {urllib.parse.urlparse(url).netloc}. "
            "The service or your network refused the request."
        ) from exc


def weather_description(code):
    descriptions = {
        0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Fog", 48: "Depositing rime fog",
        51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle",
        56: "Light freezing drizzle", 57: "Freezing drizzle",
        61: "Light rain", 63: "Rain", 65: "Heavy rain",
        66: "Light freezing rain", 67: "Freezing rain",
        71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
        80: "Rain showers", 81: "Showers", 82: "Violent rain showers",
        85: "Snow showers", 86: "Heavy snow showers",
        95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Severe thunderstorm",
    }
    return descriptions.get(code, "Weather unavailable")


def weather_emoji(code, is_day=1):
    if code == 0:
        return "☀" if is_day else "☾"
    if code in (1, 2):
        return "🌤" if is_day else "☁"
    if code == 3:
        return "☁"
    if code in (45, 48):
        return "🌫"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return "🌧"
    if code in (71, 73, 75, 77, 85, 86):
        return "❄"
    if code >= 95:
        return "⛈"
    return "🌡"


THEMES = {
    "Midnight": {"bg": "#101827", "panel": "#192438", "fg": "#F3F6FC",
                 "muted": "#A8B5C8", "accent": "#72D6C9"},
    "Light": {"bg": "#F2F5F9", "panel": "#FFFFFF", "fg": "#1C2636",
              "muted": "#657187", "accent": "#087E8B"},
    "Ocean": {"bg": "#082F49", "panel": "#0C4A6E", "fg": "#F0F9FF",
              "muted": "#BAE6FD", "accent": "#67E8F9"},
    "Forest": {"bg": "#14251D", "panel": "#20382B", "fg": "#F0FDF4",
               "muted": "#BBF7D0", "accent": "#86EFAC"},
    "Rose": {"bg": "#2A1724", "panel": "#432338", "fg": "#FFF1F2",
             "muted": "#FECDD3", "accent": "#FDA4AF"},
}


class WeatherStation:
    def __init__(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self.settings = {
            "theme": "Midnight", "width": 360, "height": 610,
            "opacity": 0.96, "refresh_minutes": 15, "always_on_top": True,
            "fahrenheit": False, "wind_mph": False, "x": 40, "y": 60,
            "manual_city": ""
        }
        self.load_settings()
        self.data = None
        self.location = "Finding your location…"
        self.busy = False
        self.refresh_job = None

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry(
            f"{self.settings['width']}x{self.settings['height']}"
            f"+{self.settings.get('x', 40)}+{self.settings.get('y', 60)}"
        )
        self.root.minsize(300, 400)
        self.root.attributes("-topmost", self.settings["always_on_top"])
        self.root.attributes("-alpha", self.settings["opacity"])
        self.root.configure(bg=self.theme["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self.hide_or_close)
        self.root.bind("<Button-3>", self.show_context_menu)
        self.root.bind("<Configure>", self.on_geometry)
        self.build_ui()
        self.apply_theme()
        self.refresh_weather()
        self.schedule_refresh()

    @property
    def theme(self):
        return THEMES.get(self.settings.get("theme"), THEMES["Midnight"])

    def load_settings(self):
        try:
            if CONFIG_FILE.exists():
                saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                self.settings.update(saved)
        except Exception:
            pass

    def save_settings(self):
        try:
            CONFIG_FILE.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
        except Exception:
            pass

    def on_geometry(self, event):
        if event.widget is self.root and event.width > 100 and event.height > 100:
            self.settings["width"] = event.width
            self.settings["height"] = event.height
            try:
                self.settings["x"] = self.root.winfo_x()
                self.settings["y"] = self.root.winfo_y()
            except tk.TclError:
                pass
            self.save_settings()

    def build_ui(self):
        self.outer = tk.Frame(self.root, bd=0)
        self.outer.pack(fill="both", expand=True)
        self.header = tk.Frame(self.outer, padx=16, pady=12)
        self.header.pack(fill="x")
        self.app_label = tk.Label(self.header, text="WEATHER STATION",
                                  font=("Segoe UI", 9, "bold"))
        self.app_label.pack(side="left")
        self.refresh_button = tk.Button(self.header, text="↻", width=3, bd=0,
                                        command=self.refresh_weather,
                                        font=("Segoe UI", 13))
        self.refresh_button.pack(side="right")
        self.pin_button = tk.Button(self.header, text="◆", width=3, bd=0,
                                    command=self.toggle_topmost)
        self.pin_button.pack(side="right", padx=(0, 5))

        self.city_label = tk.Label(self.outer, text=self.location,
                                   font=("Segoe UI", 12), anchor="w",
                                   padx=18, pady=2)
        self.city_label.pack(fill="x")
        self.date_label = tk.Label(self.outer, text="", font=("Segoe UI", 9),
                                   anchor="w", padx=18)
        self.date_label.pack(fill="x", pady=(0, 6))

        self.current_panel = tk.Frame(self.outer, padx=16, pady=14)
        self.current_panel.pack(fill="x", padx=12, pady=5)
        self.condition_label = tk.Label(self.current_panel, text="Loading weather…",
                                        font=("Segoe UI", 11))
        self.condition_label.pack(anchor="w")
        self.temp_label = tk.Label(self.current_panel, text="--°",
                                   font=("Segoe UI", 42, "bold"))
        self.temp_label.pack(anchor="w", pady=(2, 0))
        self.feels_label = tk.Label(self.current_panel, text="Feels like --°",
                                    font=("Segoe UI", 10))
        self.feels_label.pack(anchor="w")
        self.highlow_label = tk.Label(self.current_panel, text="Today: -- / --",
                                      font=("Segoe UI", 10))
        self.highlow_label.pack(anchor="w", pady=(5, 0))

        self.metrics_panel = tk.Frame(self.outer, padx=10, pady=5)
        self.metrics_panel.pack(fill="x", padx=8)
        self.metric_labels = {}
        metrics = [
            ("Humidity", "humidity"), ("Dew point", "dew"),
            ("Wind", "wind"), ("Gusts", "gust"),
            ("Pressure", "pressure"), ("Visibility", "visibility"),
            ("UV index", "uv"), ("Precipitation", "precip"),
        ]
        for i, (label, key) in enumerate(metrics):
            cell = tk.Frame(self.metrics_panel, padx=8, pady=7)
            cell.grid(row=i // 2, column=i % 2, sticky="nsew", padx=3, pady=3)
            title = tk.Label(cell, text=label.upper(), font=("Segoe UI", 8, "bold"))
            title.pack(anchor="w")
            value = tk.Label(cell, text="--", font=("Segoe UI", 11, "bold"))
            value.pack(anchor="w", pady=(2, 0))
            self.metric_labels[key] = (cell, title, value)
        self.metrics_panel.columnconfigure(0, weight=1)
        self.metrics_panel.columnconfigure(1, weight=1)

        self.sun_label = tk.Label(self.outer, text="Sunrise --   •   Sunset --",
                                  font=("Segoe UI", 9), anchor="w", padx=18)
        self.sun_label.pack(fill="x", pady=(3, 7))

        self.hourly_title = tk.Label(self.outer, text="NEXT HOURS",
                                     font=("Segoe UI", 9, "bold"), anchor="w", padx=18)
        self.hourly_title.pack(fill="x", pady=(4, 3))
        self.hourly_frame = tk.Frame(self.outer, padx=12, pady=2)
        self.hourly_frame.pack(fill="x")
        self.hourly_labels = []

        self.daily_title = tk.Label(self.outer, text="7-DAY FORECAST",
                                    font=("Segoe UI", 9, "bold"), anchor="w", padx=18)
        self.daily_title.pack(fill="x", pady=(8, 3))
        self.daily_frame = tk.Frame(self.outer, padx=12, pady=2)
        self.daily_frame.pack(fill="both", expand=True)
        self.daily_labels = []

        self.status_label = tk.Label(self.outer, text="Connecting…",
                                     font=("Segoe UI", 8), anchor="w", padx=18, pady=7)
        self.status_label.pack(fill="x", side="bottom")

    def apply_theme(self):
        t = self.theme
        self.root.configure(bg=t["bg"])
        self.outer.configure(bg=t["bg"])
        for widget in (self.header, self.metrics_panel, self.hourly_frame, self.daily_frame):
            widget.configure(bg=t["bg"])
        for widget in (self.app_label, self.city_label, self.date_label, self.condition_label,
                       self.temp_label, self.feels_label, self.highlow_label, self.sun_label,
                       self.hourly_title, self.daily_title, self.status_label):
            widget.configure(bg=t["bg"], fg=t["fg"])
        for widget in (self.current_panel,):
            widget.configure(bg=t["panel"])
        for child in self.current_panel.winfo_children():
            child.configure(bg=t["panel"], fg=t["fg"])
        for cell, title, value in self.metric_labels.values():
            cell.configure(bg=t["panel"])
            title.configure(bg=t["panel"], fg=t["muted"])
            value.configure(bg=t["panel"], fg=t["fg"])
        for widget in (self.refresh_button, self.pin_button):
            widget.configure(bg=t["panel"], fg=t["accent"], activebackground=t["bg"],
                             activeforeground=t["fg"], relief="flat",
                             highlightthickness=0, cursor="hand2")
        for row in self.hourly_labels + self.daily_labels:
            for widget in row:
                widget.configure(bg=t["panel"], fg=t["fg"])

    def show_context_menu(self, event=None):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Refresh weather now", command=self.refresh_weather)
        menu.add_command(label="Change location…", command=self.change_location)
        menu.add_separator()
        themes = tk.Menu(menu, tearoff=0)
        for name in THEMES:
            themes.add_radiobutton(label=name, value=name,
                                   variable=tk.StringVar(value=self.settings["theme"]),
                                   command=lambda n=name: self.set_theme(n))
        menu.add_cascade(label="Color theme", menu=themes)
        size_menu = tk.Menu(menu, tearoff=0)
        for label, w, h in [
            ("Compact (300 × 500)", 300, 500),
            ("Standard (360 × 610)", 360, 610),
            ("Large (420 × 760)", 420, 760),
            ("Wide (520 × 620)", 520, 620),
            ("Custom dimensions…", None, None),
        ]:
            if w is None:
                size_menu.add_command(label=label, command=self.custom_size)
            else:
                size_menu.add_command(label=label, command=lambda a=w, b=h: self.set_size(a, b))
        menu.add_cascade(label="Widget size", menu=size_menu)
        opacity_menu = tk.Menu(menu, tearoff=0)
        for label, value in [("100% (opaque)", 1.0), ("95%", .95), ("85%", .85), ("75%", .75)]:
            opacity_menu.add_command(label=label, command=lambda v=value: self.set_opacity(v))
        menu.add_cascade(label="Transparency", menu=opacity_menu)
        menu.add_separator()
        menu.add_command(label=f"Refresh interval ({self.settings['refresh_minutes']} min)…",
                         command=self.change_refresh_interval)
        menu.add_command(label="Temperature units: " + ("°F" if self.settings["fahrenheit"] else "°C"),
                         command=self.toggle_temperature_units)
        menu.add_command(label="Wind units: " + ("mph" if self.settings["wind_mph"] else "km/h"),
                         command=self.toggle_wind_units)
        menu.add_command(label=("Disable" if self.settings["always_on_top"] else "Enable") +
                         " always on top", command=self.toggle_topmost)
        menu.add_command(label="Start with Windows: " +
                         ("On" if self.startup_enabled() else "Off"),
                         command=self.toggle_startup)
        menu.add_separator()
        menu.add_command(label="Hide widget", command=self.root.withdraw)
        menu.add_command(label="Exit", command=self.exit_app)
        try:
            menu.tk_popup(event.x_root if event else self.root.winfo_pointerx(),
                          event.y_root if event else self.root.winfo_pointery())
        finally:
            menu.grab_release()

    def set_theme(self, name):
        self.settings["theme"] = name
        self.save_settings()
        self.apply_theme()

    def set_size(self, width, height):
        self.settings["width"], self.settings["height"] = width, height
        self.root.geometry(f"{width}x{height}")
        self.save_settings()

    def custom_size(self):
        width = simpledialog.askinteger("Widget width", "Width in pixels (300–900):",
                                        initialvalue=self.root.winfo_width(),
                                        minvalue=300, maxvalue=900, parent=self.root)
        if width is None:
            return
        height = simpledialog.askinteger("Widget height", "Height in pixels (400–1200):",
                                         initialvalue=self.root.winfo_height(),
                                         minvalue=400, maxvalue=1200, parent=self.root)
        if height is not None:
            self.set_size(width, height)

    def set_opacity(self, value):
        self.settings["opacity"] = value
        self.root.attributes("-alpha", value)
        self.save_settings()

    def toggle_topmost(self):
        self.settings["always_on_top"] = not self.settings["always_on_top"]
        self.root.attributes("-topmost", self.settings["always_on_top"])
        self.save_settings()

    def toggle_temperature_units(self):
        self.settings["fahrenheit"] = not self.settings["fahrenheit"]
        self.save_settings()
        self.refresh_weather()

    def toggle_wind_units(self):
        self.settings["wind_mph"] = not self.settings["wind_mph"]
        self.save_settings()
        self.refresh_weather()

    def change_refresh_interval(self):
        value = simpledialog.askinteger(
            "Refresh interval", "Refresh every how many minutes? (5–120)",
            initialvalue=self.settings["refresh_minutes"], minvalue=5, maxvalue=120,
            parent=self.root)
        if value:
            self.settings["refresh_minutes"] = value
            self.save_settings()
            self.schedule_refresh()

    def change_location(self):
        city = simpledialog.askstring(
            "Weather location",
            "Enter a city or leave blank to use your current IP location:",
            initialvalue=self.settings.get("manual_city", ""), parent=self.root)
        if city is not None:
            self.settings["manual_city"] = city.strip()
            self.save_settings()
            self.refresh_weather()

    def startup_path(self):
        return Path(os.environ.get("APPDATA", str(Path.home()))) / \
            "Microsoft/Windows/Start Menu/Programs/Startup" / "DesktopWeatherStation.bat"

    def startup_enabled(self):
        return self.startup_path().exists()

    def toggle_startup(self):
        if os.name != "nt":
            messagebox.showinfo("Startup", "Automatic startup setup is supported on Windows.")
            return
        path = self.startup_path()
        try:
            if path.exists():
                path.unlink()
                messagebox.showinfo("Startup", "Removed from Windows Startup.")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                script = Path(sys.argv[0]).resolve()
                # Use pythonw.exe where available so startup does not open a console.
                python_exe = Path(sys.executable)
                pythonw = python_exe.with_name("pythonw.exe")
                executable = pythonw if pythonw.exists() else python_exe
                content = f'@echo off\r\nstart "" "{executable}" "{script}"\r\n'
                path.write_text(content, encoding="utf-8")
                messagebox.showinfo("Startup", "Added to Windows Startup. It will launch when you sign in.")
        except Exception as exc:
            messagebox.showerror("Startup error", str(exc))

    def hide_or_close(self):
        self.root.withdraw()

    def exit_app(self):
        if self.refresh_job:
            try:
                self.root.after_cancel(self.refresh_job)
            except tk.TclError:
                pass
        self.save_settings()
        self.root.destroy()

    def schedule_refresh(self):
        if self.refresh_job:
            try:
                self.root.after_cancel(self.refresh_job)
            except tk.TclError:
                pass
        self.refresh_job = self.root.after(
            self.settings["refresh_minutes"] * 60 * 1000, self.refresh_weather
        )

    def refresh_weather(self):
        if self.busy:
            return
        self.busy = True
        self.status_label.configure(text="Updating weather…")
        threading.Thread(target=self.fetch_weather, daemon=True).start()

    def fetch_weather(self):
        try:
            manual = self.settings.get("manual_city", "").strip()
            if manual:
                geo_url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
                    {"name": manual, "count": 1, "language": "en", "format": "json"})
                geo = http_json(geo_url)
                results = geo.get("results") or []
                if not results:
                    raise RuntimeError(f"Could not find the location '{manual}'.")
                place = results[0]
                lat, lon = place["latitude"], place["longitude"]
                city = ", ".join(filter(None, [place.get("name"), place.get("admin1"), place.get("country")]))
                timezone = place.get("timezone", "auto")
            else:
                # Try two IP geolocation services. Some networks or providers
                # block one service, so the second one may still work.
                geo = None
                geo_errors = []
                for geo_url in ("https://ipwho.is/", "http://ip-api.com/json/"):
                    try:
                        candidate = http_json(geo_url)
                        if candidate.get("success", True) and candidate.get("latitude", candidate.get("lat")) is not None:
                            geo = candidate
                            break
                        geo_errors.append(str(candidate.get("message", "Location lookup failed")))
                    except Exception as geo_exc:
                        geo_errors.append(str(geo_exc))
                if geo is None:
                    raise RuntimeError(
                        "Could not determine location from your IP. Right-click the widget → "
                        "Change location and enter your city. Details: " + "; ".join(geo_errors)
                    )
                lat = geo.get("latitude", geo.get("lat"))
                lon = geo.get("longitude", geo.get("lon"))
                city = ", ".join(filter(None, [
                    geo.get("city"),
                    geo.get("region", geo.get("regionName")),
                    geo.get("country", geo.get("country_name"))
                ]))
                timezone = "auto"

            params = {
                "latitude": lat, "longitude": lon, "timezone": timezone,
                "forecast_days": 7,
                "current": ",".join([
                    "temperature_2m", "relative_humidity_2m", "apparent_temperature",
                    "is_day", "precipitation", "rain", "showers", "snowfall",
                    "weather_code", "cloud_cover", "pressure_msl", "surface_pressure",
                    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
                ]),
                "hourly": ",".join([
                    "temperature_2m", "relative_humidity_2m", "dew_point_2m",
                    "apparent_temperature", "precipitation_probability", "precipitation",
                    "weather_code", "visibility", "uv_index", "wind_speed_10m",
                ]),
                "daily": ",".join([
                    "weather_code", "temperature_2m_max", "temperature_2m_min",
                    "precipitation_probability_max", "precipitation_sum", "sunrise", "sunset",
                    "uv_index_max",
                ]),
                "temperature_unit": "fahrenheit" if self.settings["fahrenheit"] else "celsius",
                "wind_speed_unit": "mph" if self.settings["wind_mph"] else "kmh",
            }
            url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
            weather = http_json(url, timeout=20)
            self.root.after(0, lambda: self.update_ui(city or "Your location", weather))
        except Exception as exc:
            msg = str(exc)
            self.root.after(0, lambda m=msg: self.show_error(m))
        finally:
            self.root.after(0, self.finish_refresh)

    def finish_refresh(self):
        self.busy = False
        self.schedule_refresh()

    def show_error(self, error):
        # Keep the widget usable and show the actual failing service in the
        # status line instead of repeatedly interrupting the user with popups.
        self.status_label.configure(text=("Update failed: " + error)[:180])
        self.condition_label.configure(text="Weather unavailable — right-click to retry")

    def fmt(self, value, suffix="", decimals=0):
        if value is None:
            return "—"
        try:
            if decimals:
                return f"{float(value):.{decimals}f}{suffix}"
            return f"{round(float(value))}{suffix}"
        except (TypeError, ValueError):
            return "—"

    def update_ui(self, city, d):
        self.data = d
        current, hourly, daily = d["current"], d["hourly"], d["daily"]
        code = int(current.get("weather_code", 0))
        unit = "°F" if self.settings["fahrenheit"] else "°C"
        wind_unit = "mph" if self.settings["wind_mph"] else "km/h"
        self.city_label.configure(text=city)
        self.date_label.configure(text=datetime.now().strftime("%A, %d %B  •  %I:%M %p"))
        self.condition_label.configure(text=f"{weather_emoji(code, current.get('is_day', 1))}  {weather_description(code)}")
        self.temp_label.configure(text=self.fmt(current.get("temperature_2m"), unit))
        self.feels_label.configure(text=f"Feels like {self.fmt(current.get('apparent_temperature'), unit)}")
        self.highlow_label.configure(
            text=f"Today  ↑ {self.fmt(daily['temperature_2m_max'][0], unit)}"
                 f"   ↓ {self.fmt(daily['temperature_2m_min'][0], unit)}"
                 f"   •   Clouds {self.fmt(current.get('cloud_cover'), '%')}"
        )
        now_time = current.get("time", "")
        index = 0
        for i, stamp in enumerate(hourly.get("time", [])):
            if stamp >= now_time:
                index = i
                break
        dew = hourly.get("dew_point_2m", [])
        visibility = hourly.get("visibility", [])
        uv = hourly.get("uv_index", [])
        precip_prob = hourly.get("precipitation_probability", [])
        wind_hourly = hourly.get("wind_speed_10m", [])
        def at(arr, i=index):
            return arr[i] if arr and i < len(arr) else None
        metrics = {
            "humidity": self.fmt(current.get("relative_humidity_2m"), "%"),
            "dew": self.fmt(at(dew), unit),
            "wind": f"{self.fmt(current.get('wind_speed_10m'))} {wind_unit} {self.wind_direction(current.get('wind_direction_10m'))}",
            "gust": f"{self.fmt(current.get('wind_gusts_10m'))} {wind_unit}",
            "pressure": self.fmt(current.get("pressure_msl"), " hPa"),
            "visibility": self.fmt((at(visibility) / 1000) if at(visibility) is not None else None, " km", 1),
            "uv": self.fmt(at(uv), "", 1),
            "precip": f"{self.fmt(current.get('precipitation'), ' mm', 1)} / {self.fmt(at(precip_prob), '%')} chance",
        }
        for key, value in metrics.items():
            self.metric_labels[key][2].configure(text=value)
        self.sun_label.configure(
            text=f"Sunrise {self.clock(daily.get('sunrise', [''])[0])}"
                 f"   •   Sunset {self.clock(daily.get('sunset', [''])[0])}"
                 f"   •   UV max {self.fmt(daily.get('uv_index_max', [None])[0], '', 1)}"
        )
        self.draw_hourly(hourly, index, unit, wind_unit)
        self.draw_daily(daily, unit)
        self.apply_theme()
        self.status_label.configure(text=f"Updated {datetime.now().strftime('%I:%M:%S %p')}  •  Right-click for settings")

    @staticmethod
    def clock(value):
        try:
            return datetime.fromisoformat(value).strftime("%I:%M %p").lstrip("0")
        except Exception:
            return "—"

    @staticmethod
    def wind_direction(degrees):
        if degrees is None:
            return ""
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return dirs[round(float(degrees) / 45) % 8]

    def draw_hourly(self, hourly, start, unit, wind_unit):
        for child in self.hourly_frame.winfo_children():
            child.destroy()
        self.hourly_labels = []
        t = self.theme
        for i in range(start, min(start + 5, len(hourly.get("time", [])))):
            card = tk.Frame(self.hourly_frame, bg=t["panel"], padx=7, pady=7)
            card.pack(side="left", fill="both", expand=True, padx=2)
            stamp = hourly["time"][i]
            try:
                label = datetime.fromisoformat(stamp).strftime("%I %p").lstrip("0")
            except Exception:
                label = stamp[-5:]
            code = hourly["weather_code"][i]
            values = [
                (label, 8), (weather_emoji(code), 15),
                (self.fmt(hourly["temperature_2m"][i], unit), 10),
                (f"💧{self.fmt(hourly['precipitation_probability'][i], '%')}", 8),
                (f"{self.fmt(hourly['wind_speed_10m'][i])} {wind_unit}", 8),
            ]
            for text, size in values:
                lab = tk.Label(card, text=text, bg=t["panel"], fg=t["fg"],
                               font=("Segoe UI", size))
                lab.pack(pady=1)
                self.hourly_labels.append([lab])
        self.apply_theme()

    def draw_daily(self, daily, unit):
        for child in self.daily_frame.winfo_children():
            child.destroy()
        self.daily_labels = []
        t = self.theme
        for i, stamp in enumerate(daily.get("time", [])[:7]):
            try:
                day = datetime.fromisoformat(stamp).strftime("%a  %d")
            except Exception:
                day = stamp
            code = daily["weather_code"][i]
            row = tk.Frame(self.daily_frame, bg=t["panel"], padx=8, pady=4)
            row.pack(fill="x", pady=2)
            rain = daily["precipitation_probability_max"][i]
            rain_text = f"{self.fmt(rain, '%')} rain"
            items = [
                (day, 9, "w"), (weather_emoji(code), 13, "w"),
                (weather_description(code), 9, "w"),
                (f"{self.fmt(daily['temperature_2m_max'][i], unit)} / "
                 f"{self.fmt(daily['temperature_2m_min'][i], unit)}", 9, "e"),
                (rain_text, 8, "e"),
            ]
            for text, size, side in items:
                lab = tk.Label(row, text=text, bg=t["panel"], fg=t["fg"],
                               font=("Segoe UI", size), anchor=side)
                lab.pack(side="left" if side == "w" else "right", padx=(0, 6))
                self.daily_labels.append([lab])
        self.apply_theme()


if __name__ == "__main__":
    try:
        app = WeatherStation()
        app.root.mainloop()
    except Exception as error:
        try:
            messagebox.showerror(APP_NAME, str(error))
        except Exception:
            print(error)
