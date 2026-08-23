import {
  FrontendRenderer,
  FrontendRendererArgs,
} from "@streamlit/component-v2-lib";

import { NomadText, NomadTextData, NomadTextState } from "./nomad-text";
import "./nomad-text.css";

const instances = new WeakMap<FrontendRendererArgs["parentElement"], NomadText>();

const render: FrontendRenderer<NomadTextState, NomadTextData> = (args) => {
  const { data, parentElement } = args;
  const root = parentElement.querySelector<HTMLElement>(".nomad-text-root");
  if (!root) throw new Error("Nomad Text root was not found.");

  let instance = instances.get(parentElement);
  if (!instance) {
    instance = new NomadText(root, data);
    instances.set(parentElement, instance);
  } else {
    instance.update(data);
  }

  return () => {
    const current = instances.get(parentElement);
    if (current) current.destroy();
    instances.delete(parentElement);
  };
};

export default render;
