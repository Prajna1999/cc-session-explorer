import { NextResponse } from "next/server"
import { cookies } from "next/headers"

import { AUTH_COOKIE } from "@/lib/sessions"

export async function POST() {
  const store = await cookies()
  store.delete(AUTH_COOKIE)
  return NextResponse.json({ ok: true })
}
