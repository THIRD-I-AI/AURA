import React from 'react';
import '../../styles/components.css';

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  helpText?: string;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
}

/**
 * Text Input Component
 */
export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  (
    {
      label,
      error,
      helpText,
      leftIcon,
      rightIcon,
      className = '',
      ...props
    },
    ref
  ) => {
    return (
      <div className="input-group">
        {label && <label>{label}</label>}
        <div
          style={{
            position: 'relative',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          {leftIcon && (
            <span
              style={{
                position: 'absolute',
                left: 'var(--space-3)',
                display: 'flex',
                pointerEvents: 'none',
              }}
            >
              {leftIcon}
            </span>
          )}
          <input
            ref={ref}
            className={`input ${leftIcon ? 'pl-10' : ''} ${rightIcon ? 'pr-10' : ''} ${className}`}
            style={leftIcon ? { paddingLeft: '2.5rem' } : rightIcon ? { paddingRight: '2.5rem' } : {}}
            {...props}
          />
          {rightIcon && (
            <span
              style={{
                position: 'absolute',
                right: 'var(--space-3)',
                display: 'flex',
                pointerEvents: 'none',
              }}
            >
              {rightIcon}
            </span>
          )}
        </div>
        {error && <span className="help-text error">{error}</span>}
        {helpText && !error && <span className="help-text">{helpText}</span>}
      </div>
    );
  }
);

Input.displayName = 'Input';

export default Input;
