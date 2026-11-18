import { create } from "zustand";

/** App-wide overlays that more than one place can open (the palette, the HALT dialog, the
 *  mobile navigation sheet). Transient by design: none of these survive a reload. */
interface Ui {
  palette: boolean;
  halt: boolean;
  /** The off-canvas navigation sheet on small screens. */
  navOpen: boolean;
  setPalette: (open: boolean) => void;
  setHalt: (open: boolean) => void;
  setNavOpen: (open: boolean) => void;
}

export const useUi = create<Ui>((set) => ({
  palette: false,
  halt: false,
  navOpen: false,
  setPalette: (palette) => set({ palette }),
  setHalt: (halt) => set({ halt }),
  setNavOpen: (navOpen) => set({ navOpen }),
}));
