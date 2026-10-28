import { Moon, Sun } from 'lucide-react';
import { useTheme } from '@/contexts/ThemeContext';
import { cn } from '@/lib/cn';

/** Switches between the dark and light themes; the label names the theme it
 *  switches to, so a screen reader hears the action, not the state. */
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggleTheme } = useTheme();
  const next = theme === 'dark' ? 'light' : 'dark';
  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      data-testid="theme-toggle"
      className={cn(
        'inline-flex size-10 items-center justify-center rounded-none text-text-secondary transition-colors',
        'hover:text-text-primary focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
        className,
      )}
    >
      {theme === 'dark' ? <Sun className="size-5" aria-hidden="true" /> : <Moon className="size-5" aria-hidden="true" />}
    </button>
  );
}
