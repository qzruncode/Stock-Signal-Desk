import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Button } from '../Button';

describe('Button', () => {
  it('renders children', () => {
    render(<Button>Click me</Button>);

    expect(screen.getByRole('button', { name: 'Click me' })).toBeInTheDocument();
  });

  it('uses button type by default and exposes the selected variant', () => {
    render(<Button variant="danger">Delete</Button>);

    const button = screen.getByRole('button', { name: 'Delete' });
    expect(button).toHaveAttribute('type', 'button');
    expect(button).toHaveAttribute('data-variant', 'danger');
    expect(button.className).toContain('bg-destructive');
    expect(button).toHaveAttribute('data-slot', 'button');
  });

  it('disables the button when loading and shows loading text', () => {
    render(<Button isLoading loadingText="Saving">Save</Button>);

    const button = screen.getByRole('button', { name: /saving/i });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByText('Saving')).toBeInTheDocument();
  });

  it.each([
    ['primary', 'bg-primary'],
    ['secondary', 'bg-secondary'],
    ['ghost', 'hover:bg-accent'],
    ['danger', 'bg-destructive'],
  ] as const)('supports the %s variant with expected styling', (variant, expectedClass) => {
    render(<Button variant={variant}>Action</Button>);

    const button = screen.getByRole('button', { name: 'Action' });
    expect(button).toHaveAttribute('data-variant', variant);
    expect(button.className).toContain(expectedClass);
  });
});
