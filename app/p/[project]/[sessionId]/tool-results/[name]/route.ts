import { NextResponse } from "next/server"
import { toolResultText, NotFoundError } from "@/lib/sessions"

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ project: string; sessionId: string; name: string }> }
) {
  const { project, sessionId, name } = await params
  try {
    const text = await toolResultText(project, sessionId, name)
    return new NextResponse(text, { headers: { "content-type": "text/plain; charset=utf-8" } })
  } catch (e) {
    if (e instanceof NotFoundError) return new NextResponse("Not found", { status: 404 })
    throw e
  }
}
