import { forwardRef } from 'react';
import type React from 'react';
import { Label as LabelPrimitive } from 'radix-ui';
import { cn } from '../../utils/cn';

export const Label = forwardRef<
  React.ElementRef<typeof LabelPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root>
>(({ className, ...props }, ref) => (
  <LabelPrimitive.Root
    ref={ref}
    data-slot="label"
    className={cn('text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70', className)}
    {...props}
  />
));

Label.displayName = LabelPrimitive.Root.displayName;
