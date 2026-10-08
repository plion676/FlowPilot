import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ToolManager } from "./ToolManager";
import { EndpointManager } from "./EndpointManager";
import type { AgentProfile, McpEndpoint } from "./api";

function setupApi(conflict = false) {
  const endpoints: McpEndpoint[] = [
    {
      id: "business",
      name: "综合业务",
      role_id: "consultant",
      url: "http://127.0.0.1:3100/mcp/consultant",
      credential_profile: "local",
      enabled: true,
      version: 1,
      execution_revision: 1,
    },
    {
      id: "crm",
      name: "客户服务",
      role_id: "consultant",
      url: "http://127.0.0.1:3100/mcp/consultant_aux",
      credential_profile: "local",
      enabled: true,
      version: 1,
      execution_revision: 1,
    },
  ];
  endpoints.push({
    ...endpoints[0],
    id: "production",
    name: "生产角色连接",
    role_id: "production",
    url: "http://127.0.0.1:3100/mcp/production",
  });
  const agent: AgentProfile = {
    id: "consultant",
    name: "运营顾问",
    version: 1,
    endpoint_ids: ["business"],
    tools: [{ endpoint_id: "business", tool_name: "crm.get_customer_overview" }],
    endpoints: [endpoints[0]],
  };
  const tools = (id: string) => [
    {
      name: "crm.get_customer_overview",
      domain: "crm",
      description: "查询客户基础信息",
      endpoint_id: id,
      endpoint_name: id === "crm" ? "客户服务" : "综合业务",
      endpoint_role_id: "consultant",
      bindable: true,
      available: true,
      status: "ready",
      risk_level: "read_only",
      input_schema: { type: "object" },
    },
  ];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/agents"))
      return Response.json({
        agents: [
          agent,
          {
            ...agent,
            id: "production",
            name: "生产角色",
            endpoint_ids: [],
            tools: [],
            endpoints: [],
          },
        ],
      });
    if (path.endsWith("/capabilities")) {
      if (conflict)
        return Response.json(
          { error: { code: "BINDING_CONFLICT", message: "配置已被修改，请刷新后重新选择。" } },
          { status: 409 },
        );
      const payload = JSON.parse(init!.body as string);
      agent.tools = payload.tools;
      agent.endpoint_ids = payload.endpoint_ids;
      agent.version++;
      return Response.json(agent);
    }
    if (path.endsWith("/tools") || path.endsWith("/check")) {
      const id = path.split("/").at(-2)!;
      return Response.json({ tools: tools(id), stale: false, status: "ready" });
    }
    if (init?.method === "PUT")
      return Response.json({
        ...JSON.parse(init.body as string),
        version: 2,
        execution_revision: 1,
      });
    return Response.json({
      endpoints,
      checks: [],
      credential_profiles: [{ id: "local", configured: true }],
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  return fetchMock;
}

describe("Endpoint-aware capabilities", () => {
  it("only offers connections owned by the selected role", async () => {
    setupApi();
    const user = userEvent.setup();
    render(<ToolManager />);
    await screen.findByRole("checkbox", { name: "连接 business" });
    expect(screen.queryByRole("checkbox", { name: "连接 production" })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("当前 Agent"), "production");
    expect(screen.getByRole("checkbox", { name: "连接 production" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "连接 business" })).not.toBeInTheDocument();
  });
  it("filters the Endpoint directory by role, not Tool domain", async () => {
    setupApi();
    const user = userEvent.setup();
    render(<EndpointManager />);
    await screen.findByRole("button", { name: "编辑 production" });
    await user.selectOptions(
      screen.getByRole("combobox", { name: "连接所属角色筛选" }),
      "production",
    );
    expect(screen.queryByRole("button", { name: "编辑 business" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编辑 production" })).toBeInTheDocument();
  });
  it("selects a source before tools and saves independent same-name bindings", async () => {
    const fetchMock = setupApi();
    const user = userEvent.setup();
    render(<ToolManager />);
    await screen.findByText("查询客户基础信息");
    expect(
      screen.getByRole("checkbox", { name: "绑定 business / crm.get_customer_overview" }),
    ).toBeChecked();
    expect(
      screen.queryByRole("checkbox", { name: "绑定 crm / crm.get_customer_overview" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "连接 crm" }));
    await user.click(
      screen.getByRole("checkbox", { name: "绑定 crm / crm.get_customer_overview" }),
    );
    await user.click(screen.getByRole("button", { name: "保存工具绑定" }));
    expect(await screen.findByRole("status")).toHaveTextContent("已保存");
    const saved = fetchMock.mock.calls.find(([path]) => String(path).endsWith("/capabilities"));
    expect(JSON.parse(saved![1]!.body as string)).toMatchObject({
      endpoint_ids: ["business", "crm"],
      expected_version: 1,
      expected_endpoint_versions: { business: 1, crm: 1 },
      tools: [
        { endpoint_id: "business", tool_name: "crm.get_customer_overview" },
        { endpoint_id: "crm", tool_name: "crm.get_customer_overview" },
      ],
    });
    await user.type(screen.getByRole("searchbox", { name: "搜索工具" }), "综合业务");
    expect(
      screen.queryByRole("checkbox", { name: "绑定 crm / crm.get_customer_overview" }),
    ).not.toBeInTheDocument();
  });
  it("removes Endpoint tools after confirmation and reports stale save", async () => {
    setupApi(true);
    const user = userEvent.setup();
    render(<ToolManager />);
    await screen.findByText("查询客户基础信息");
    await user.click(screen.getByRole("checkbox", { name: "连接 business" }));
    expect(window.confirm).toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "保存工具绑定" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("配置已被修改");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "保存工具绑定" })).toBeEnabled());
  });
  it("searches role connections, edits without secrets and explicitly checks", async () => {
    const fetchMock = setupApi();
    const user = userEvent.setup();
    render(<EndpointManager />);
    await screen.findByRole("button", { name: "编辑 crm" });
    await user.type(screen.getByRole("searchbox", { name: "搜索连接" }), "客户");
    expect(screen.queryByRole("button", { name: "编辑 business" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑 crm" }));
    expect(screen.getByRole("textbox", { name: "Endpoint ID" })).toBeDisabled();
    expect(screen.queryByRole("textbox", { name: "连接分类名称" })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "所属角色" }), "consultant");
    await user.type(screen.getByRole("textbox", { name: "连接名称" }), "备用");
    await user.click(screen.getByRole("button", { name: "保存连接" }));
    expect(await screen.findByRole("status")).toHaveTextContent("连接已保存");
    const saved = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(saved![1]!.body as string)).toMatchObject({
      role_id: "consultant",
      credential_profile: "local",
      expected_version: 1,
    });
    await user.click(screen.getByRole("button", { name: "检查 crm" }));
    expect(await screen.findByRole("status")).toHaveTextContent("检查成功");
  });
});
