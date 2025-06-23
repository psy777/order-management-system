'use client';

import React, { createContext, useContext, useState } from 'react';
import { OrderFormData } from '../components/views';

interface AppContextProps {
  orderForEmailModal: OrderFormData | null;
  setOrderForEmailModal: React.Dispatch<React.SetStateAction<OrderFormData | null>>;
  appSettings: any;
  setAppSettings: React.Dispatch<React.SetStateAction<any>>;
  allSelectableItems: any;
  setAllSelectableItems: React.Dispatch<React.SetStateAction<any>>;
  handleOrderSent: (updatedOrder: OrderFormData) => void;
}

const AppContext = createContext<AppContextProps | undefined>(undefined);

export const AppProvider = ({ children }: { children: React.ReactNode }) => {
  const [orderForEmailModal, setOrderForEmailModal] = useState<OrderFormData | null>(null);
  const [appSettings, setAppSettings] = useState<any>({ company_name: "", default_email_body: "" });
  const [allSelectableItems, setAllSelectableItems] = useState<any>({});

  const handleOrderSent = (updatedOrder: OrderFormData) => {
    // This will be handled by the page
  };

  return (
    <AppContext.Provider
      value={{
        orderForEmailModal,
        setOrderForEmailModal,
        appSettings,
        setAppSettings,
        allSelectableItems,
        setAllSelectableItems,
        handleOrderSent,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};

export const useAppContext = () => {
  const context = useContext(AppContext);
  if (context === undefined) {
    throw new Error('useAppContext must be used within an AppProvider');
  }
  return context;
};
