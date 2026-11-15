import { useEffect, useState } from "react";
import { live, type LiveStatus } from "../services/ws";

export function useLiveStatus(): LiveStatus {
  const [s, setS] = useState<LiveStatus>(live.status);
  useEffect(() => live.onStatus(setS), []);
  return s;
}
