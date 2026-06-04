import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import LoginPage from "./page";

vi.mock("@/lib/auth-flow", () => ({
  loginUser: vi.fn(),
}));

describe("LoginPage", () => {
  it("renders the public login form and password reset affordance", () => {
    const markup = renderToStaticMarkup(<LoginPage />);

    expect(markup).toContain("OMEGA");
    expect(markup).toContain("Inicia sesión para continuar");
    expect(markup).toContain('type="email"');
    expect(markup).toContain('type="password"');
    expect(markup).toContain("Iniciar sesión");
    expect(markup).toContain("¿Olvidaste tu contraseña?");
    expect(markup).toContain('href="/forgot-password"');
    expect(markup).toContain("Ir al formulario");
  });
});
