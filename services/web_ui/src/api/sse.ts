import { request } from "./client";
import type { ChatQueryParams, Source } from "../types/api";

export type ChatEvent =
  | { type: "sources"; data: Source[] }
  | { type: "token"; data: string }
  | { type: "done" }
  | { type: "error"; data: string };

/**
 * Stream SSE events from /chat endpoint using an AsyncGenerator.
 */
export async function* streamChat(
  params: ChatQueryParams,
  signal?: AbortSignal
): AsyncGenerator<ChatEvent, void, unknown> {
  const response = await request("/chat", {
    method: "POST",
    body: JSON.stringify({
      query: params.query,
      top_k: params.top_k ?? 5,
      temperature: params.temperature ?? 0.7,
      stream: params.stream ?? true,
    }),
    signal,
  });

  if (!response.body) {
    throw new Error("ReadableStream not supported on response body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");

  let buffer = "";
  let currentEventType = "";
  let currentDataParts: string[] = [];

  function parseEvent(): ChatEvent | null {
    if (!currentEventType || currentDataParts.length === 0) {
      return null;
    }
    const dataStr = currentDataParts.join("\n");
    const eventType = currentEventType;

    // Reset state for next event
    currentEventType = "";
    currentDataParts = [];

    if (eventType === "sources") {
      try {
        return { type: "sources", data: JSON.parse(dataStr) as Source[] };
      } catch {
        return { type: "sources", data: [] };
      }
    } else if (eventType === "token") {
      try {
        // Token might be a JSON string or raw text
        const parsed = JSON.parse(dataStr);
        return { type: "token", data: typeof parsed === "string" ? parsed : dataStr };
      } catch {
        return { type: "token", data: dataStr };
      }
    } else if (eventType === "done") {
      return { type: "done" };
    } else if (eventType === "error") {
      return { type: "error", data: dataStr };
    }

    return null;
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (let line of lines) {
        if (line.endsWith("\r")) {
          line = line.slice(0, -1);
        }

        if (line.trim() === "") {
          const ev = parseEvent();
          if (ev) yield ev;
        } else if (line.startsWith("event:")) {
          let val = line.slice(6);
          if (val.startsWith(" ")) val = val.slice(1);
          currentEventType = val;
        } else if (line.startsWith("data:")) {
          let val = line.slice(5);
          if (val.startsWith(" ")) val = val.slice(1);
          currentDataParts.push(val);
        }
      }
    }

    // Flush remaining buffer if any
    const trailingEvent = parseEvent();
    if (trailingEvent) {
      yield trailingEvent;
    }

    // Always signal completion
    yield { type: "done" };
  } finally {
    reader.releaseLock();
  }
}
