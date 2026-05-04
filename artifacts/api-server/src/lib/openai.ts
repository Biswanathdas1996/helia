import { openai } from "@workspace/integrations-openai-ai-server";

export const CHAT_MODEL = "gpt-4o-mini";

export type ChatTurn = { role: "system" | "user" | "assistant"; content: string };

export async function chat(messages: ChatTurn[], opts?: { json?: boolean }) {
  const res = await openai.chat.completions.create({
    model: CHAT_MODEL,
    messages,
    temperature: 0.2,
    ...(opts?.json ? { response_format: { type: "json_object" as const } } : {}),
  });
  return res.choices[0]?.message?.content?.trim() ?? "";
}
