export function distance(a, b) {
  const rad = Math.PI / 180,
    p = a.latitude * rad,
    q = b.latitude * rad;
  const h =
    Math.sin((q - p) / 2) ** 2 +
    Math.cos(p) *
      Math.cos(q) *
      Math.sin(((b.longitude - a.longitude) * rad) / 2) ** 2;
  return 12742000 * Math.asin(Math.sqrt(Math.min(1, h)));
}
export class RoutePlan {
  constructor(map) {
    this.map = map;
    this.points = [];
    this.speed = 5;
  }
  add(point, two = false) {
    if (two && this.points.length >= 2)
      throw new Error(
        "Two Spot accepts two points. Clear the route to start again.",
      );
    if (this.points.length >= 10000)
      throw new Error("Maximum 10,000 waypoints");
    const coordinates = { latitude: point.latitude, longitude: point.longitude };
    if (this.points.length && distance(this.points.at(-1), coordinates) < 0.01)
      throw new Error("Choose a different location for the next waypoint");
    this.points.push(coordinates);
    this.render();
  }
  remove(index) {
    if (!Number.isInteger(index) || index < 0 || index >= this.points.length)
      return;
    this.points.splice(index, 1);
    this.render();
  }
  move(index, offset) {
    const target = index + offset;
    if (index < 0 || target < 0 || target >= this.points.length) return;
    [this.points[index], this.points[target]] = [
      this.points[target],
      this.points[index],
    ];
    this.render();
  }
  render(updateMap = true) {
    if (updateMap) {
      this.map.route(this.points, (index) => this.remove(index));
    }
    const meters = this.points
      .slice(1)
      .reduce((sum, p, i) => sum + distance(this.points[i], p), 0);
    const loops = Number(document.querySelector("#loops")?.value || 1);
    const closure =
      this.points.length > 1 ? distance(this.points.at(-1), this.points[0]) : 0;
    const total = loops
      ? meters * loops + closure * Math.max(0, loops - 1)
      : meters + closure;
    const summary = document.querySelector("#route-summary");
    if (summary) {
      summary.textContent =
        `${this.points.length} waypoints · ${(total / 1000).toFixed(2)} km${loops ? "" : " / cycle"} · ${Math.ceil(total / (this.speed / 3.6) / 60)} min`;
    }
    const list = document.querySelector("#waypoints");
    list.replaceChildren();
    this.points.slice(0, 50).forEach((p, index) => {
      const li = document.createElement("li");
      li.value = index + 1;
      li.textContent = `${p.latitude.toFixed(5)}, ${p.longitude.toFixed(5)}`;
      for (const [label, offset] of [["↑", -1], ["↓", 1], ["Remove", 0]]) {
        const button = document.createElement("button");
        button.textContent = label;
        button.type = "button";
        button.setAttribute("aria-label", `${label} waypoint ${index + 1}`);
        button.disabled = offset !== 0 && (index + offset < 0 || index + offset >= this.points.length);
        button.onclick = () => {
          if (!offset) this.remove(index);
          else this.move(index, offset);
        };
        li.append(button);
      }
      list.append(li);
    });
    if (this.points.length > 50) {
      const li = document.createElement("li");
      li.textContent = `… ${this.points.length - 50} more points`;
      list.append(li);
    }
  }
}
