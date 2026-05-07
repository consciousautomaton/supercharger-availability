export function canonicalNetworkId(raw: string | null | undefined): string {
  const low = (raw ?? "").toLowerCase();
  const rules: Array<[RegExp, string]> = [
    [/\btesla\b/, "tesla"],
    [/\bchargepoint\b/, "chargepoint"],
    [/\bionity\b/, "ionity"],
    [/\bfastned\b/, "fastned"],
    [/\belectrify america\b/, "electrify-america"],
    [/\belectrify canada\b/, "electrify-canada"],
    [/\bevgo\b|\bevgo network\b/, "evgo"],
    [/\bflo\b/, "flo"],
    [/circuit.*lectrique/, "circuit-electrique"],
    [/\blink\b/, "blink"],
    [/\bvolta\b/, "volta"],
    [/\bshell\b/, "shell-recharge"],
    [/\bbp\b|\baral pulse\b/, "bp-pulse"],
    [/\benbw\b/, "enbw"],
    [/\be\.?on\b/, "eon"],
    [/\ballego\b/, "allego"],
    [/\belectra\b/, "electra"],
    [/\bizivia\b/, "izivia"],
    [/\bfreshmile\b/, "freshmile"],
    [/\btotal\b/, "totalenergies"],
    [/\bengie\b/, "engie"],
    [/\bmer\b/, "mer"],
    [/\bewe go\b/, "ewe-go"],
    [/\bstadtwerke\b/, "stadtwerke"],
    [/\bnon-networked\b/, "non-networked"],
  ];
  for (const [pattern, id] of rules) {
    if (pattern.test(low)) return id;
  }
  const cleaned = low.replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return `raw:${cleaned || "unknown"}`;
}

