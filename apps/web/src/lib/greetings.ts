/** Time-aware greetings for the home page: 4 day slots x 5 lines, one
 * picked at random per visit. Short, research-flavoured, first-name based. */

const MORNING = [
  "Good morning, {name}.",
  "Morning, {name}.",
  "Rise and research, {name}.",
  "Fresh start, {name}.",
  "Bright and early, {name}.",
];

const AFTERNOON = [
  "Good afternoon, {name}.",
  "Afternoon, {name}.",
  "Hey {name}.",
  "Welcome back, {name}.",
  "Back at it, {name}.",
];

const EVENING = [
  "Good evening, {name}.",
  "Evening, {name}.",
  "Winding down, {name}?",
  "Still curious, {name}?",
  "Evening reading, {name}?",
];

const NIGHT = [
  "Still up, {name}?",
  "Late one, {name}?",
  "Midnight oil, {name}?",
  "Quiet hours, {name}.",
  "Night owl, {name}?",
];

const GERMAN = {
  morning: ["Guten Morgen, {name}.", "Morgen, {name}.", "Bereit für neue Evidenz, {name}?"],
  afternoon: ["Guten Tag, {name}.", "Hallo {name}.", "Willkommen zurück, {name}."],
  evening: ["Guten Abend, {name}.", "Noch neugierig, {name}?", "Abendlektüre, {name}?"],
  night: ["Noch wach, {name}?", "Eine späte Recherche, {name}?", "Ruhige Stunden, {name}."],
};

function slotFor(hour: number): string[] {
  if (hour >= 5 && hour < 11) return MORNING;
  if (hour >= 11 && hour < 17) return AFTERNOON;
  if (hour >= 17 && hour < 22) return EVENING;
  return NIGHT;
}

export function pickGreeting(name: string, now: Date = new Date()): string {
  const lines = slotFor(now.getHours());
  const line = lines[Math.floor(Math.random() * lines.length)];
  return line.replace("{name}", name);
}

export function pickGermanGreeting(name: string, now: Date = new Date()): string {
  const hour = now.getHours();
  const lines =
    hour >= 5 && hour < 11
      ? GERMAN.morning
      : hour >= 11 && hour < 17
        ? GERMAN.afternoon
        : hour >= 17 && hour < 22
          ? GERMAN.evening
          : GERMAN.night;
  const line = lines[Math.floor(Math.random() * lines.length)];
  return line.replace("{name}", name);
}
