import React from 'react';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export const Button: React.FC<ButtonProps> = ({
  variant = 'primary',
  size = 'md',
  className,
  type = 'button',
  children,
  ...rest
}) => (
  <button
    type={type}
    className={['ui-btn', `ui-btn--${variant}`, `ui-btn--${size}`, className]
      .filter(Boolean)
      .join(' ')}
    {...rest}
  >
    {children}
  </button>
);
