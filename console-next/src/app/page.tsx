import { redirect } from "next/navigation";

/**
 * Root entry — sends the user to /dashboard. The middleware will
 * bounce them to /login first if they're not authenticated, so this
 * file is trivially correct in either state.
 */
export default function RootPage() {
  redirect("/dashboard");
}
