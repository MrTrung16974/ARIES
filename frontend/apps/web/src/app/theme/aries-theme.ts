import { definePreset } from '@primeuix/themes';
import Aura from '@primeuix/themes/aura';

/** Theme ARIES: màu chủ đạo hồng neon theo logo (đầu cừu phát sáng trên nền đêm). */
export const AriesPreset = definePreset(Aura, {
  semantic: {
    primary: {
      50: '{pink.50}',
      100: '{pink.100}',
      200: '{pink.200}',
      300: '{pink.300}',
      400: '{pink.400}',
      500: '{pink.500}',
      600: '{pink.600}',
      700: '{pink.700}',
      800: '{pink.800}',
      900: '{pink.900}',
      950: '{pink.950}',
    },
  },
});
