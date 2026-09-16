export class LocationMap {
  constructor(onSelect, actions, toast) {
    this.onSelect = onSelect;
    this.actions = actions;
    this.toast = toast;
    if (!window.L) {
      toast("Map library unavailable. You can still enter coordinates.");
      return;
    }

    let initialCenter = [13.7563, 100.5018];
    let initialZoom = 14;
    try {
      const savedView = localStorage.getItem("last_map_view");
      if (savedView) {
        const parsed = JSON.parse(savedView);
        if (Number.isFinite(parsed.lat) && Number.isFinite(parsed.lng)) {
          initialCenter = [parsed.lat, parsed.lng];
          if (Number.isFinite(parsed.zoom)) initialZoom = parsed.zoom;
        }
      }
    } catch (_) {}

    this.map = L.map("map", { zoomControl: false, doubleClickZoom: false }).setView(
      initialCenter,
      initialZoom,
    );
    L.control.zoom({ position: "topright" }).addTo(this.map);

    this.map.on("moveend zoomend", () => {
      try {
        const center = this.map.getCenter();
        localStorage.setItem(
          "last_map_view",
          JSON.stringify({ lat: center.lat, lng: center.lng, zoom: this.map.getZoom() }),
        );
      } catch (_) {}
    });

    let warned = false;
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    })
      .on("tileerror", () => {
        if (!warned) {
          warned = true;
          toast(
            "Map tiles unavailable. Check your internet connection; coordinate controls still work.",
          );
        }
      })
      .addTo(this.map);
    this.map.on("click", (e) =>
      this.select({ latitude: e.latlng.lat, longitude: e.latlng.lng }, false, true),
    );
  }
  select(point, pan = false, openPopup = false) {
    // Leaflet can expose wrapped longitudes after panning across the antimeridian.
    point = {
      latitude: point.latitude,
      longitude: ((((point.longitude + 180) % 360) + 360) % 360) - 180,
    };
    try {
      localStorage.setItem("last_destination", JSON.stringify(point));
    } catch (_) {}
    this.onSelect(point);
    if (!this.map) return;
    if (this.destination) this.destination.remove();
    this.destination = L.marker([point.latitude, point.longitude], {
      draggable: true,
      icon: L.divIcon({ className: "destination-pin", iconSize: [15, 15] }),
    }).addTo(this.map);
    this.destination.on("dragend", (e) => {
      const p = e.target.getLatLng();
      this.select({ latitude: p.lat, longitude: p.lng });
    });
    const popup = document.createElement("div");
    const label = document.createElement("div");
    label.textContent = `${point.latitude.toFixed(6)}, ${point.longitude.toFixed(6)}`;
    popup.append(label);
    const copy = document.createElement("button");
    copy.type = "button";
    copy.textContent = "Copy lat, long";
    copy.onclick = async () => {
      try {
        await navigator.clipboard.writeText(label.textContent);
        this.toast("Coordinates copied");
      } catch {
        this.toast("Could not copy coordinates. Copy the coordinates shown above manually.");
      }
    };
    popup.append(copy);
    for (const [title, action] of [
      ["Teleport here", "teleport"],
      ["Run here", "run"],
      ["Add to route", "route"],
      ["Add favorite", "favorite"],
    ]) {
      const button = document.createElement("button");
      button.textContent = title;
      button.onclick = () => {
        this.actions[action](point);
        this.map.closePopup();
      };
      popup.append(button);
    }
    this.destination.bindPopup(popup, { autoPan: false });
    if (openPopup) this.destination.openPopup();
    if (pan)
      this.map.setView(
        [point.latitude, point.longitude],
        Math.max(14, this.map.getZoom()),
      );
  }
  update(point) {
    if (!this.map) return;
    if (!point || point.latitude === null) {
      this.current?.remove();
      this.current = null;
      return;
    }
    const latlng = [point.latitude, point.longitude];
    if (!this.current)
      this.current = L.marker(latlng, {
        icon: L.divIcon({ className: "current-pin", iconSize: [20, 20] }),
      }).addTo(this.map);
    else this.current.setLatLng(latlng);
  }
  route(points, onRemove) {
    if (!this.map) return;
    if (!points || !points.length) {
      this.clearRoute();
      return;
    }
    const pointsKey = points
      .map((p) => `${Number(p.latitude).toFixed(6)},${Number(p.longitude).toFixed(6)}`)
      .join(";");
    if (this._currentRouteKey === pointsKey) return;
    this._currentRouteKey = pointsKey;
    this.polyline?.remove();
    this.waypoints?.remove();
    this.waypoints = L.layerGroup().addTo(this.map);
    points.forEach((p, i) => {
      const label = String(i + 1);
      const width = label.length === 1 ? 24 : label.length === 2 ? 30 : 38;
      const marker = L.marker([p.latitude, p.longitude], {
        icon: L.divIcon({
          className: "route-pin",
          html: label,
          iconSize: [width, 24],
          iconAnchor: [width / 2, 12],
        }),
      }).bindTooltip(`Waypoint ${label} · click to manage`);
      const popup = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = `Waypoint ${label}`;
      const coordinates = document.createElement("div");
      coordinates.textContent = `${p.latitude.toFixed(6)}, ${p.longitude.toFixed(6)}`;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Remove waypoint";
      remove.onclick = () => {
        this.map.closePopup();
        onRemove?.(i);
      };
      popup.append(title, coordinates, remove);
      marker.bindPopup(popup).addTo(this.waypoints);
    });
    this.polyline = L.polyline(
      points.map((p) => [p.latitude, p.longitude]),
      { color: "#2c855a", weight: 3, dashArray: "7 6" },
    ).addTo(this.map);
  }
  fit(points) {
    if (this.map && points.length)
      this.map.fitBounds(
        points.map((p) => [p.latitude, p.longitude]),
        { padding: [70, 70], maxZoom: 16 },
      );
  }
  center() {
    if (this.current) this.map.panTo(this.current.getLatLng());
  }
  setRadiusCircle(center, radius_m) {
    if (!this.map || !center || center.latitude == null || center.longitude == null) return;
    const latlng = [center.latitude, center.longitude];
    if (!this.radiusCircle) {
      this.radiusCircle = L.circle(latlng, {
        radius: radius_m,
        color: "#267c57",
        fillColor: "#3da66c",
        fillOpacity: 0.12,
        weight: 1.5,
        dashArray: "6 6",
      }).addTo(this.map);
    } else {
      this.radiusCircle.setLatLng(latlng);
      this.radiusCircle.setRadius(radius_m);
    }
  }
  clearRadiusCircle() {
    if (this.radiusCircle) {
      this.radiusCircle.remove();
      this.radiusCircle = null;
    }
  }
  clearRoute() {
    this._currentRouteKey = null;
    this.polyline?.remove();
    this.polyline = null;
    this.waypoints?.remove();
    this.waypoints = null;
  }
  clearDestination() {
    if (this.destination) {
      this.destination.remove();
      this.destination = null;
    }
  }
}
