import personaData from "../personas/personas.json";

export type Persona = {
  userId: string;
  userName: string;
  avatarUrl: string;
  bio: string;
};

const registry: Record<string, Persona> = Object.fromEntries(
  (personaData as Persona[]).map((persona) => [persona.userId, persona])
);

export const personas = Object.values(registry);

export function getPersona(userId?: string): Persona {
  if (!userId) {
    return { userId: "unknown", userName: "Unknown User", avatarUrl: "", bio: "" };
  }
  return registry[userId] ?? { userId, userName: "Unknown User", avatarUrl: "", bio: "" };
}
