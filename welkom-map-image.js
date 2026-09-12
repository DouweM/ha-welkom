// ha-map-card plugin: overlay one of a welkom home's images on the map.
//
// The image and where it sits are welkom's to know — a home's `attrs.images`
// entry carries the file and its map bounds — so the dashboard only names
// which one it wants:
//
//   plugins:
//   - name: aerial
//     url: /welkom/map-image.js
//     options: {home: oasis, image: aerial}   # image defaults to "aerial"
//
// The plugin asks the integration for the image's manifest (its same-origin
// URL and bounds) and hands both to Leaflet. Moving the house's drone shot,
// swapping it for a better one, or re-measuring its corners is a change in
// welkom.yml, and every map picks it up.
export default function (L, pluginBase) {
  return class WelkomImagePlugin extends pluginBase {
    constructor(map, name, options = {}) {
      super(map, name, options);
      this.home = options.home;
      this.image = options.image ?? "aerial";
      this.opacity = options.opacity ?? 1;
      this.layer = null;
    }

    async renderMap() {
      if (!this.home) {
        console.warn(`[welkom-map-image] plugin ${this.name}: no home configured`);
        return;
      }
      const path = `/welkom/homes/${encodeURIComponent(this.home)}/images/${encodeURIComponent(this.image)}.json`;
      const response = await fetch(path, { cache: "no-cache" });
      if (!response.ok) {
        console.warn(`[welkom-map-image] plugin ${this.name}: ${path} answered ${response.status}`);
        return;
      }
      const { url, bounds } = await response.json();
      if (!bounds) {
        console.warn(`[welkom-map-image] plugin ${this.name}: image ${this.image} has no bounds`);
        return;
      }
      this.layer = L.imageOverlay(url, bounds, { opacity: this.opacity });
      this.layer.addTo(this.map);
    }

    destroy() {
      this.layer?.remove();
      this.layer = null;
    }
  };
}
