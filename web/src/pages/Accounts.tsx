import { useStore } from "../lib/store";
import { ago, money } from "../lib/format";
import { Card, Empty } from "../components/ui";

export function Accounts() {
  const { accounts, risk } = useStore();
  const limits = new Map((risk?.limits ?? []).map((l) => [l.account_id, l]));
  return (
    <Card title="Cuentas sincronizadas">
      {accounts.length === 0 ? <Empty>Esperando datos del bróker…</Empty> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Cuenta</th><th className="num">Balance</th><th className="num">P&L día</th><th>Riesgo</th><th>Actualizada</th></tr></thead>
            <tbody>
              {accounts.map((a) => {
                const l = limits.get(a.account_id);
                return (
                  <tr key={a.account_id}>
                    <td><b>{a.account_id}</b></td>
                    <td className="num">{money(a.balance)}</td>
                    <td className={`num ${a.daily_pnl < 0 ? "bad" : a.daily_pnl > 0 ? "ok" : ""}`}>{money(a.daily_pnl)}</td>
                    <td>{l ? (l.trading_halted ? <span className="badge bad">pausada</span> : <span className="badge ok">límites</span>) : <span className="badge muted">sin límites</span>}</td>
                    <td className="muted">{ago(a.updated_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
