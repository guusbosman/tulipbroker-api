import React, { createContext, useContext, useState, type ReactNode } from "react";
import { personas, type Persona } from "./personas";

type PersonaContextValue = {
  activePersona: Persona;
  setActivePersona: (persona: Persona) => void;
};

const PersonaContext = createContext<PersonaContextValue | undefined>(undefined);

export function PersonaProvider({ children }: { children: ReactNode }) {
  const [activePersona, setActivePersona] = useState<Persona>(personas[0]);

  return (
    <PersonaContext.Provider value={{ activePersona, setActivePersona }}>
      {children}
    </PersonaContext.Provider>
  );
}

export function usePersona() {
  const ctx = useContext(PersonaContext);
  if (!ctx) {
    throw new Error("usePersona must be used within a PersonaProvider");
  }
  return ctx;
}
