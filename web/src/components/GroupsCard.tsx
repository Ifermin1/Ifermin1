import { useEffect, useState } from "react";
import { useStore } from "../lib/store";
import type { Group, GroupFollower, Groups } from "../lib/api";
import { Card } from "./ui";
import { Icon } from "./Icons";

/* Grupos de líderes: una configuración completa de copia (líder + seguidoras con multiplicador) guardada con nombre.
 * El addon copia una sola líder a la vez, así que activar un grupo cambia la líder y deja copiando solo a sus seguidoras;
 * las reglas de los demás grupos se apagan, no se borran. */
export function GroupsCard({ compact = false }: { compact?: boolean }) {
  const { client, accounts, guard, refresh, setRules, readOnly, health } = useStore();
  const [st, setSt] = useState<Groups | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<Group | "new" | null>(null);
  const load = () => client?.groups().then(setSt).catch(() => {});
  useEffect(() => { void load(); }, [client, health?.bridge.master_account]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!client || !st) return null;

  const run = async (label: string, fn: () => Promise<unknown>, ok: string) => {
    if (!(await guard())) return;
    setBusy(label); setErr(null); setMsg(null);
    try { await fn(); await load(); setRules(await client.rules()); await refresh(); if (ok) setMsg(ok); }
    catch (ex) { setErr(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); }
  };
  const name = (acc: string) => accounts.find((a) => a.account_id === acc)?.alias || acc;
  const saveCurrent = () => {
    if (!st.current.master) { setErr("No hay líder detectada: elige una en el desplegable de arriba."); return; }
    const n = prompt(`Nombre para este grupo (líder ${name(st.current.master)}, ${st.current.followers.length} seguidoras copiando):`, "");
    if (!n || !n.trim()) return;
    void run("save", () => client.groupSaveCurrent(n.trim()), `Grupo «${n.trim()}» guardado.`);
  };
  const activate = (g: Group) => {
    const others = st.current.master && st.current.master.toLowerCase() !== g.master.toLowerCase() ? `\n\nLa líder pasa de ${name(st.current.master)} a ${name(g.master)}; las seguidoras de los demás grupos dejan de copiar (sus reglas quedan apagadas, no se borran).` : "";
    if (!confirm(`¿Activar el grupo «${g.name}»?\n\nLíder ${name(g.master)} → ${g.followers.map((f) => `${name(f.account)} ×${f.multiplier}`).join(", ") || "sin seguidoras"}.${others}`)) return;
    void run(g.id, () => client.groupActivate(g.id), `Grupo «${g.name}» activo.`);
  };
  const remove = (g: Group) => { if (confirm(`¿Eliminar el grupo «${g.name}»? Las reglas de copia no se tocan.`)) void run(g.id, () => client.groupDelete(g.id), `Grupo «${g.name}» eliminado.`); };
  const active = st.groups.find((g) => g.id === st.active_id);

  return (
    <Card title={active ? `Grupo activo: «${active.name}»${st.drift ? " · cambiado a mano" : ""}` : "Grupos de líderes"} icon="layers" right={<span className="chips">
            {!readOnly && <button className="chip-btn" disabled={busy !== null} onClick={saveCurrent} data-testid="group-save-current" title="Guarda la líder actual y las seguidoras que copian ahora como un grupo con nombre">Guardar lo de ahora como grupo</button>}
            {!readOnly && <button className="chip-btn" disabled={busy !== null} onClick={() => setEditing("new")} data-testid="group-new">Nuevo grupo</button>}
          </span>}>
      {!compact && <p className="muted small" style={{ margin: "0 0 8px" }}>Un grupo es una líder con sus seguidoras y multiplicadores. Guarda varios (mañana/tarde, 50K/100K, cada dueño…) y cambia de uno a otro con un clic. {st.note}</p>}
      {st.drift && active && <p className="warn small" data-testid="group-drift">La líder o las reglas cambiaron a mano desde que activaste «{active.name}». Vuelve a activarlo para dejarlo como estaba, o guarda lo de ahora como otro grupo.</p>}
      {st.groups.length === 0 && <p className="muted small" data-testid="groups-empty">Todavía no hay grupos. {st.current.master ? `Ahora mismo la líder es ${name(st.current.master)} con ${st.current.followers.length} seguidoras copiando: púlsalo arriba para guardarlo con nombre.` : "Elige una líder y vincula sus seguidoras, y guárdalo con nombre."}</p>}
      {st.groups.length > 0 && (
        <div className="groups-list" data-testid="groups-list">
          {st.groups.map((g) => {
            const isActive = g.id === st.active_id;
            return (
              <div key={g.id} className={`group-row ${isActive ? "active" : ""}`} data-testid={`group-${g.id}`}>
                <div className="group-main">
                  <b>{isActive && <Icon name="crown" size={12} />} {g.name}</b>
                  <span className="small muted">líder <b>{name(g.master)}</b> → {g.followers.length ? g.followers.map((f) => `${name(f.account)}${f.multiplier !== 1 ? ` ×${f.multiplier}` : ""}`).join(", ") : "sin seguidoras"}</span>
                </div>
                <div className="chips">
                  {!isActive && !readOnly && <button className="primary small-btn" disabled={busy !== null} onClick={() => activate(g)} data-testid={`group-activate-${g.id}`}>Activar</button>}
                  {isActive && st.drift && !readOnly && <button className="small-btn" disabled={busy !== null} onClick={() => activate(g)} data-testid={`group-activate-${g.id}`}>Volver a aplicar</button>}
                  {isActive && !st.drift && <span className="badge ok">activo</span>}
                  {!readOnly && <button className="ghost small-btn" disabled={busy !== null} onClick={() => setEditing(g)} data-testid={`group-edit-${g.id}`}>Editar</button>}
                  {!readOnly && <button className="ghost small-btn" disabled={busy !== null} onClick={() => remove(g)} title="Eliminar el grupo (las reglas no se tocan)">✕</button>}
                </div>
              </div>
            );
          })}
        </div>
      )}
      {editing && <GroupEditor group={editing === "new" ? null : editing} onClose={() => setEditing(null)}
                               onSave={async (body) => { await run("edit", () => editing === "new" ? client.groupCreate(body) : client.groupUpdate((editing as Group).id, body), `Grupo «${body.name}» guardado.`); setEditing(null); }} />}
      {err && <p className="error small" data-testid="group-error">{err}</p>}
      {msg && !err && <p className="ok small" data-testid="group-msg">{msg}</p>}
    </Card>
  );
}

function GroupEditor({ group, onClose, onSave }: { group: Group | null; onClose: () => void; onSave: (body: { name: string; master: string; followers: GroupFollower[] }) => Promise<void> }) {
  const { accounts, health } = useStore();
  const [name, setName] = useState(group?.name ?? "");
  const [master, setMaster] = useState(group?.master ?? health?.bridge.master_account ?? "");
  const [followers, setFollowers] = useState<GroupFollower[]>(group?.followers ?? []);
  const toggle = (acc: string, on: boolean) => setFollowers(on ? [...followers.filter((f) => f.account !== acc), { account: acc, multiplier: 1 }] : followers.filter((f) => f.account !== acc));
  const setMult = (acc: string, m: number) => setFollowers(followers.map((f) => (f.account === acc ? { ...f, multiplier: Math.max(1, Math.round(m) || 1) } : f)));
  const visible = accounts.filter((a) => a.enabled || followers.some((f) => f.account === a.account_id) || a.account_id === master);
  return (
    <div className="group-editor" data-testid="group-editor">
      <div className="rule-form">
        <label>Nombre del grupo<input value={name} onChange={(e) => setName(e.target.value)} placeholder="p. ej. Mañana, Cuentas 50K, Juan" data-testid="group-name" /></label>
        <label>Líder<select value={master} onChange={(e) => { setMaster(e.target.value); setFollowers(followers.filter((f) => f.account !== e.target.value)); }} data-testid="group-master">
          <option value="">Elige la líder…</option>
          {accounts.map((a) => <option key={a.account_id} value={a.account_id}>{a.alias || a.account_id}{a.alias ? ` · ${a.account_id}` : ""}</option>)}
        </select></label>
      </div>
      <table className="guardian-table">
        <thead><tr><th>Copia</th><th>Seguidora</th><th className="num">Multiplicador</th></tr></thead>
        <tbody>
          {visible.filter((a) => a.account_id !== master).map((a) => {
            const f = followers.find((x) => x.account === a.account_id);
            return (
              <tr key={a.account_id}>
                <td><input type="checkbox" checked={!!f} onChange={(e) => toggle(a.account_id, e.target.checked)} data-testid={`group-f-${a.account_id}`} /></td>
                <td>{a.alias || a.account_id}{a.alias ? <span className="muted small"> · {a.account_id}</span> : null}{!a.enabled && <span className="chip" style={{ marginLeft: 6 }}>oculta</span>}</td>
                <td className="num">{f && <input className="mult" inputMode="numeric" step={1} min={1} type="number" value={f.multiplier} onChange={(e) => setMult(a.account_id, Number(e.target.value))} data-testid={`group-m-${a.account_id}`} />}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="chips" style={{ marginTop: 8 }}>
        <button className="primary small-btn" disabled={!name.trim() || !master} onClick={() => void onSave({ name: name.trim(), master, followers })} data-testid="group-editor-save">Guardar grupo</button>
        <button className="ghost small-btn" onClick={onClose}>Cancelar</button>
      </div>
    </div>
  );
}
