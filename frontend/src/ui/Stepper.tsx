import React from 'react';

export const Stepper: React.FC<{ steps: string[]; current: number }> = ({ steps, current }) => (
  <ol className="ui-stepper">
    {steps.map((label, i) => {
      const state = i < current ? 'done' : i === current ? 'current' : 'todo';
      return (
        <li
          key={label}
          className={`ui-step ui-step--${state}`}
          aria-current={state === 'current' ? 'step' : undefined}
        >
          <span aria-hidden="true" className="ui-step__marker">
            {state === 'done' ? '✓' : i + 1}
          </span>
          <span className="ui-step__label">{label}</span>
        </li>
      );
    })}
  </ol>
);
