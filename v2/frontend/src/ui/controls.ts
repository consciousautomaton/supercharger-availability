import type { AppState, CountryCatalogEntry, DatasetFilter, Mode } from "../data/types";

export interface ControlElements {
  region: HTMLSelectElement;
  dataset: HTMLSelectElement;
  mode: HTMLSelectElement;
  yearControl: HTMLElement;
  yearSlider: HTMLInputElement;
  yearValue: HTMLElement;
  distance: HTMLSelectElement;
}

export function bindControls(
  state: AppState,
  onChange: (next: AppState) => void,
): ControlElements {
  const region = require_<HTMLSelectElement>("region-select");
  const dataset = require_<HTMLSelectElement>("dataset-select");
  const mode = require_<HTMLSelectElement>("mode-select");
  const yearControl = require_<HTMLElement>("year-control");
  const yearSlider = require_<HTMLInputElement>("year-slider");
  const yearValue = require_<HTMLElement>("year-value");
  const distance = require_<HTMLSelectElement>("distance-select");

  function syncYearControl(currentMode: Mode): void {
    yearControl.hidden = currentMode !== "timeline";
  }
  syncYearControl(state.mode);

  mode.addEventListener("change", () => {
    const next: AppState = { ...state, mode: mode.value as Mode };
    state = next;
    syncYearControl(next.mode);
    onChange(next);
  });

  yearSlider.addEventListener("input", () => {
    const year = Number.parseInt(yearSlider.value, 10);
    yearValue.textContent = String(year);
    state = { ...state, year };
    onChange(state);
  });

  region.addEventListener("change", () => {
    state = { ...state, region: region.value };
    onChange(state);
  });
  dataset.addEventListener("change", () => {
    state = { ...state, dataset: dataset.value as DatasetFilter };
    onChange(state);
  });
  distance.addEventListener("change", () => {
    state = { ...state, distance_km: Number.parseInt(distance.value, 10) };
    onChange(state);
  });

  return { region, dataset, mode, yearControl, yearSlider, yearValue, distance };
}

export function populateRegionOptions(
  controls: ControlElements,
  countries: readonly CountryCatalogEntry[],
): void {
  const selected = controls.region.value;
  const options = [
    new Option("World", "world"),
    ...countries
      .filter((country) => country.has_station_data || country.has_ev_stock_data)
      .map((country) => {
        const labelParts = [country.name];
        if (country.station_count > 0) {
          labelParts.push(`${country.station_count.toLocaleString()} stations`);
        }
        return new Option(labelParts.join(" · "), country.iso_a3);
      }),
  ];
  controls.region.replaceChildren(...options);
  controls.region.disabled = false;
  controls.region.value = options.some((option) => option.value === selected)
    ? selected
    : "world";
}

function require_<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing element #${id}`);
  return el as T;
}
