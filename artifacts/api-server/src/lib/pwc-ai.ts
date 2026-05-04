const BASE_URL = "https://genai-sharedservice-americas.pwc.com";
const MODEL = "vertex_ai.gemini-2.5-flash-image";

export type ChatTurn = { role: "system" | "user" | "assistant"; content: string };

export async function chat(messages: ChatTurn[], opts?: { json?: boolean }): Promise<string> {
  const apiKey = process.env.PWC_AI_API_KEY;
  const token = process.env.PWC_TOKEN;

  if (!apiKey || !token) {
    throw new Error("PWC_AI_API_KEY or PWC_TOKEN not configured");
  }

  const body: Record<string, unknown> = {
    model: MODEL,
    messages,
    max_tokens: 2048,
    temperature: 0.2,
  };

  if (opts?.json) {
    body.response_format = { type: "json_object" };
  }

  const res = await fetch(`${BASE_URL}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
      "x-api-key": token,
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`PwC AI gateway error ${res.status}: ${text.slice(0, 300)}`);
  }

  const data = (await res.json()) as { choices?: { message?: { content?: string } }[] };
  return data.choices?.[0]?.message?.content?.trim() ?? "";
}

export async function extractTextFromBase64(
  mimeType: string,
  base64Data: string,
  filename: string,
): Promise<string> {
  const apiKey = process.env.PWC_AI_API_KEY;
  const token = process.env.PWC_TOKEN;

  if (!apiKey || !token) {
    throw new Error("PWC_AI_API_KEY or PWC_TOKEN not configured");
  }

  const isImage = mimeType.startsWith("image/");

  const userContent: unknown = isImage
    ? [
        {
          type: "image_url",
          image_url: { url: `data:${mimeType};base64,${base64Data}` },
        },
        {
          type: "text",
          text: `Extract ALL text content from this image verbatim. Include every word, number, heading, label, caption, and table cell. Output plain text only — no markdown, no commentary.`,
        },
      ]
    : [
        {
          type: "text",
          text: `The following is a base64-encoded ${mimeType} file named "${filename}".\n\nExtract ALL text content from it verbatim. Preserve headings, bullet points, table structure (as plain text), and paragraph breaks. Output plain text only — no markdown, no commentary.\n\nBase64 content:\n${base64Data}`,
        },
      ];

  const body = {
    model: MODEL,
    messages: [
      {
        role: "system",
        content:
          "You are a precise document text extractor. Extract text verbatim. Never summarise or omit content.",
      },
      { role: "user", content: userContent },
    ],
    max_tokens: 8192,
    temperature: 0,
  };

  const res = await fetch(`${BASE_URL}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
      "x-api-key": token,
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`PwC AI extraction error ${res.status}: ${text.slice(0, 300)}`);
  }

  const data = (await res.json()) as { choices?: { message?: { content?: string } }[] };
  return data.choices?.[0]?.message?.content?.trim() ?? "";
}
