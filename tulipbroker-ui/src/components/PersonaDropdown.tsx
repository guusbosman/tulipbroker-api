import React from "react";
import { personas } from "../personas";
import { usePersona } from "../PersonaContext";

export function PersonaDropdown() {
  const { activePersona, setActivePersona } = usePersona();

  return (
    <div>
      <label htmlFor="persona-select">Active user</label>
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
        {activePersona.avatarUrl ? (
          <img
            src={activePersona.avatarUrl}
            alt={`${activePersona.userName} avatar`}
            width={32}
            height={32}
            style={{ borderRadius: "50%" }}
          />
        ) : null}
        <select
          id="persona-select"
          value={activePersona.userId}
          onChange={(e) => {
            const selected = personas.find((p) => p.userId === e.target.value);
            if (selected) {
              setActivePersona(selected);
            }
          }}
        >
          {personas.map((persona) => (
            <option key={persona.userId} value={persona.userId}>
              {persona.userName}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
