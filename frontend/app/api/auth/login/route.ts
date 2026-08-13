import { NextResponse } from "next/server"
import { cookies } from "next/headers"

import { AUTH_COOKIE } from "@/lib/sessions"

const API_BASE = process.env.API_BASE_URL ?? "http://localhost:8000"

export async function POST(request: Request) {
  let body: { email?: string; password?: string }
  try {
    body = await request.json()
  } catch {
    return NextResponse.json({ error: "invalid request body" }, { status: 400 })
  }
  if (!body.email || !body.password) {
    return NextResponse.json({ error: "email and password are required" }, { status: 400 })
  }

  let res: Response
  try {
    res = await fetch(`${API_BASE}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: body.email, password: body.password }),
      cache: "no-store",
    })
  } catch {
    return NextResponse.json(
      { error: `cannot reach the backend at ${API_BASE} — is it running?` },
      { status: 502 }
    )
  }

  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "invalid email or password"
    return NextResponse.json({ error: detail }, { status: 401 })
  }

  const store = await cookies()
  store.set(AUTH_COOKIE, data.token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 24 * 7,
  })
  return NextResponse.json({ ok: true, user: data.user })
}
