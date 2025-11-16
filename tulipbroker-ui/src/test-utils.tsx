import React from "react";
import { render } from "@testing-library/react";
import { PersonaProvider } from "./PersonaContext";

export function renderWithPersona(ui: React.ReactElement) {
  return render(<PersonaProvider>{ui}</PersonaProvider>);
}
