import { useEffect, useRef, useState } from "react";

/** Devuelve `value` limitado a un cambio cada `ms` (primero inmediato, último garantizado). Para recargar datos cuando
 *  llegan fills por WebSocket sin que una ráfaga continua posponga la recarga indefinidamente (un debounce lo haría). */
export function useThrottled<T>(value: T, ms: number): T {
  const [out, setOut] = useState(value);
  const last = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    const now = Date.now();
    const wait = Math.max(0, last.current + ms - now);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { last.current = Date.now(); setOut(value); }, wait);
    return () => window.clearTimeout(timer.current);
  }, [value, ms]);
  return out;
}
