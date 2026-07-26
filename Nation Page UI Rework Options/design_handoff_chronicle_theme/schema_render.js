const strip = s => String(s == null ? '' : s).replace(/https?:\/\/\S+/g, '').replace(/\s+/g, ' ').trim();
const capWords = s => String(s).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

export function buildView(schema, item, lookups) {
  const P = schema.properties || {};
  const capFields = new Set(Object.values(P).map(p => (p && typeof p.max === 'string') ? p.max : null).filter(Boolean));
  const laws = (schema.laws || []).map(f => item[f]).filter(Boolean);
  const view = { title: item.name || item._id, subtitle: laws.join(' · '), bars: [], kvRows: [], numRows: [], chipGroups: [], mods: [], notes: [], objTables: [] };
  const fmtNum = (p, v) => p.format === 'percentage' ? Math.round((v || 0) * 100) + '%' : String(v ?? 0);
  for (const [f, p] of Object.entries(P)) {
    if (!p || p.hidden || capFields.has(f) || f === 'name') continue;
    const v = item[f];
    const label = p.label || capWords(f);
    switch (p.bsonType) {
      case 'string': case 'enum': case 'date': {
        if (p.image) break;
        if (p.long_text || (typeof v === 'string' && v.length > 140)) { if (strip(v)) view.notes.push({ title: label, text: String(v) }); break; }
        view.kvRows.push({ k: label, v: (v ?? '') === '' ? 'None' : String(v) });
        break; }
      case 'number': {
        const capF = typeof p.max === 'string' ? p.max : null;
        if (capF && item[capF] != null) view.bars.push({ label, value: v ?? 0, capv: item[capF] });
        else view.numRows.push({ k: label, v: fmtNum(p, v) + (typeof p.max === 'number' ? ' / ' + p.max : ''), breakdown: !!p.show_breakdown });
        break; }
      case 'boolean':
        view.kvRows.push({ k: label, v: v ? '✓' : '✗', color: v ? '#a8d88a' : '#e08a6a' });
        break;
      case 'linked_object': {
        const ref = v && lookups[v];
        view.kvRows.push({ k: label, v: ref ? ref.name : (v ? String(v).slice(0, 10) + '…' : (p.noneResult || 'None')), link: !!ref });
        break; }
      case 'json_resource_enum': case 'json_district_enum':
        view.kvRows.push({ k: label, v: v ? capWords(v) : 'None' });
        break;
      case 'array': {
        const it = p.items || {};
        const arr = Array.isArray(v) ? v : [];
        if (f === 'modifiers' || f === 'external_modifiers' || (it.properties && it.properties.modifier_type)) {
          arr.forEach(m => {
            let lbl = capWords(m.modifier_type || 'modifier');
            const det = m.attribute || m.resource || m.job || '';
            if (det) lbl += ' — ' + capWords(det);
            if (m.scaling && m.scaling !== 'flat') lbl += ' · ' + capWords(m.scaling).toLowerCase() + (m.scaling_extra ? ' ' + m.scaling_extra : '');
            view.mods.push({ label: lbl, v: (m.value > 0 ? '+' : '') + m.value, good: (m.value ?? 0) >= 0, dur: m.duration === -1 ? 'permanent' : m.duration + ' ticks', note: strip(m.source) || '—' });
          });
        } else if (it.bsonType === 'string') {
          const chips = arr.filter(Boolean).map(capWords);
          if (chips.length) view.chipGroups.push({ title: label, chips });
        } else if (it.bsonType === 'linked_object') {
          view.chipGroups.push({ title: label, chips: arr.map(id => (lookups[id] || {}).name).filter(Boolean) });
        } else if (arr.length) {
          view.numRows.push({ k: label, v: arr.length + ' entries' });
        }
        break; }
      case 'object': {
        const ent = Object.entries(v || {});
        if (ent.length) view.objTables.push({ title: label, rows: ent.map(([k2, v2]) => ({ k: capWords(k2), v: typeof v2 === 'object' ? JSON.stringify(v2) : String(v2) })) });
        break; }
    }
  }
  return view;
}
