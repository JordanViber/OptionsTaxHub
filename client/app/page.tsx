"use client";

import { useState } from "react";
import InstallPrompt from "./components/InstallPrompt";
import ServiceWorkerRegistration from "./components/ServiceWorkerRegistration";

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    console.log("✅ Upload button clicked! File:", file?.name);
    e.preventDefault();
    if (!file) return;

    setLoading(true);
    const formData = new FormData();
    formData.append("file", file);

    try {
      // POST to backend for CSV upload (MVP: parse and return first 5 rows)
      // This enables users to upload portfolio CSVs for tax-loss harvesting analysis
      const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";
      const response = await fetch(`${apiUrl}/upload-csv`, {
        method: "POST",
        body: formData,
      });
      const result = await response.json();
      setData(result);
    } catch (error) {
      console.error("Upload failed:", error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <ServiceWorkerRegistration />
      <InstallPrompt />
      <div className="min-h-screen flex flex-col items-center justify-center bg-gray-100 dark:bg-gray-900 p-4">
        <h1 className="text-2xl md:text-3xl font-bold mb-8 text-center">
          OptionsTaxHub – Tax-Optimized Options Trading
        </h1>
        <form
          onSubmit={handleSubmit}
          className="flex flex-col items-center space-y-4 w-full max-w-md"
        >
          <input
            type="file"
            accept=".csv"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
            className="border border-gray-300 dark:border-gray-700 rounded p-2 w-full"
          />
          <button
            type="submit"
            disabled={!file || loading}
            className="bg-blue-500 text-white px-4 py-2 rounded disabled:opacity-50 w-full"
          >
            {loading ? "Uploading..." : "Upload CSV"}
          </button>
        </form>
        {data.length > 0 && (
          <div className="mt-8 w-full max-w-4xl">
            <h2 className="text-xl font-semibold mb-4">
              Parsed Data (First 5 Rows):
            </h2>
            <pre className="bg-white dark:bg-gray-800 p-4 rounded shadow overflow-x-auto">
              {JSON.stringify(data, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </>
  );
}
